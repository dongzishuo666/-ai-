# ============================================================
# 文件：backend/app/services/official_clients/kimi_client.py
# 说明：Kimi 官方客户端。协议：OpenAI 兼容。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import KIMI, ModelConfig


async def call(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    url = f"{KIMI.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {KIMI.api_key}",
        "Content-Type": "application/json",
    }
    # Kimi K2 系列模型仅接受 temperature=1，其他值会返回 400
    _t = temperature if temperature is not None else cfg.temperature
    if _t is not None and float(_t) != 1.0:
        _t = 1.0
    payload = {
        "model": cfg.model_name,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else cfg.max_tokens,
        "temperature": _t,
    }

    resp = await client.post(
        url,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(cfg.timeout_seconds, connect=10.0),
    )
    resp.raise_for_status()
    data = resp.json()

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
