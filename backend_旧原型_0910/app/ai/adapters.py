"""八家 AI 的统一适配层。

按底层协议归为三类，避免后端代码臃肿：
- openai   ：DeepSeek / 智谱 / 文心一言 / Kimi / OpenAI / Grok（均兼容 OpenAI 协议）
- anthropic：Claude
- gemini   ：Gemini
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.ai.base import ChatMessage


async def _iter_sse(response: httpx.Response) -> AsyncIterator[str]:
    """逐行解析 SSE，产出 data 段文本。"""
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                yield line[5:].strip()


async def openai_stream(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    temperature: float,
    max_tokens: int,
) -> AsyncIterator[str]:
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [m.to_dict() for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with client.stream("POST", url, json=payload, headers=headers) as resp:
        resp.raise_for_status()
        async for data in _iter_sse(resp):
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                yield text


async def anthropic_stream(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    temperature: float,
    max_tokens: int,
) -> AsyncIterator[str]:
    url = f"{base_url}/v1/messages"
    system_parts = [m.content for m in messages if m.role == "system"]
    chat = [m.to_dict() for m in messages if m.role != "system"]
    payload = {
        "model": model,
        "messages": chat,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    async with client.stream("POST", url, json=payload, headers=headers) as resp:
        resp.raise_for_status()
        async for data in _iter_sse(resp):
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if obj.get("type") == "content_block_delta":
                text = (obj.get("delta") or {}).get("text")
                if text:
                    yield text


async def gemini_stream(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    temperature: float,
    max_tokens: int,
) -> AsyncIterator[str]:
    url = f"{base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse"
    system_parts = [m.content for m in messages if m.role == "system"]
    contents = []
    for m in messages:
        if m.role == "system":
            continue
        contents.append(
            {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
        )
    payload: dict = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    async with client.stream("POST", url, json=payload, headers=headers) as resp:
        resp.raise_for_status()
        async for data in _iter_sse(resp):
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            for cand in obj.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    text = part.get("text")
                    if text:
                        yield text
