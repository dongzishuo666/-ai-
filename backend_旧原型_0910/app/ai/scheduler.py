"""统一 AI 调度中心：模型调用、超时、重试、降级兜底。

三大功能全部复用本模块，保证「架构统一、异常处理同源」。
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx

from app.ai import adapters
from app.ai.base import ChatMessage, StreamResult
from app.ai.mock_engine import stream_text
from app.ai.registry import get_profile, is_ready, resolve_endpoint
from app.config import settings


class AIScheduler:
    """轻量调度器：单进程内共享一个 httpx 客户端连接池。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.request_timeout, connect=10.0),
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _real_stream(
        self,
        ai_id: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        profile = get_profile(ai_id)
        base_url, api_key = resolve_endpoint(profile)
        client = self.client()
        if profile.provider == "anthropic":
            gen = adapters.anthropic_stream(
                client, base_url, api_key, profile.model, messages, temperature, max_tokens
            )
        elif profile.provider == "gemini":
            gen = adapters.gemini_stream(
                client, base_url, api_key, profile.model, messages, temperature, max_tokens
            )
        else:
            gen = adapters.openai_stream(
                client, base_url, api_key, profile.model, messages, temperature, max_tokens
            )
        async for piece in gen:
            yield piece

    async def stream(
        self,
        ai_id: str,
        messages: list[ChatMessage],
        result: StreamResult | None = None,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        mock_text: str = "",
    ) -> AsyncIterator[str]:
        """流式调用指定 AI。

        :param mock_text: 模拟模式下使用的内容（真实调用时不生效）
        :param result: 调用结束后回填引擎/错误/降级信息
        """
        result = result if result is not None else StreamResult()
        result.ai_id = ai_id
        profile = get_profile(ai_id)

        fallback_text = mock_text or f"（{profile.name} 暂无可用输出）"

        if not is_ready(profile):
            result.engine = "mock"
            async for piece in stream_text(fallback_text):
                yield piece
            return

        attempts = settings.max_retries + 1
        last_error = ""
        for attempt in range(attempts):
            produced = False
            try:
                async for piece in self._real_stream(ai_id, messages, temperature, max_tokens):
                    produced = True
                    yield piece
                if produced:
                    result.engine = "real"
                    return
                last_error = "模型返回为空"
            except Exception as exc:  # noqa: BLE001 - 兜底必须捕获全部异常
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < attempts - 1:
                    continue

        result.engine = "mock"
        result.fallback = True
        result.error = last_error
        async for piece in stream_text(fallback_text):
            yield piece

    async def complete(
        self,
        ai_id: str,
        messages: list[ChatMessage],
        result: StreamResult | None = None,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        mock_text: str = "",
    ) -> str:
        """非流式调用：收集完整文本。"""
        chunks: list[str] = []
        async for piece in self.stream(
            ai_id,
            messages,
            result,
            temperature=temperature,
            max_tokens=max_tokens,
            mock_text=mock_text,
        ):
            chunks.append(piece)
        return "".join(chunks)


scheduler = AIScheduler()
