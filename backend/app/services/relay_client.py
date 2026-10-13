# ============================================================
# 文件：backend/app/services/relay_client.py
# 说明：中转渠道统一客户端。覆盖 Gemini / OpenAI / Claude / Grok。
# 协议：OpenAI Chat Completions 兼容。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import AI_CALL_TIMEOUT, RELAY, ModelConfig


async def call(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    调用中转渠道模型。
    返回：{content, prompt_tokens, completion_tokens, total_tokens}
    异常：超时 → httpx.TimeoutException；HTTP 错误 → httpx.HTTPStatusError
    """
    url = f"{RELAY.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {RELAY.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg.model_name,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else cfg.max_tokens,
        "temperature": temperature if temperature is not None else cfg.temperature,
    }

    resp = await client.post(
        url,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(AI_CALL_TIMEOUT, connect=10.0),
    )
    resp.raise_for_status()
    data = resp.json()

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
