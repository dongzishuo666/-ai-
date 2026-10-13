# ============================================================
# 文件：backend/app/services/official_clients/zhipu_client.py
# 说明：智谱官方渠道客户端。协议：OpenAI 兼容（自有域名）。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import AI_CALL_TIMEOUT, ZHIPU, ModelConfig


async def call(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    url = f"{ZHIPU.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ZHIPU.api_key}",
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

    # 智谱错误响应会在 body 内返回 error
    if "error" in data:
        raise RuntimeError(f"智谱错误: {data['error']}")

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
