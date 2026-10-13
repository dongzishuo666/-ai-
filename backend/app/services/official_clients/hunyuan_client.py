# ============================================================
# 文件：backend/app/services/official_clients/hunyuan_client.py
# 说明：腾讯混元官方渠道客户端（tokenhub 网关）。协议：OpenAI 兼容。
#       注意：hy3 为推理模型，会先输出 reasoning_content，额度过小时正文为空。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import AI_CALL_TIMEOUT, HUNYUAN, ModelConfig


async def call(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    url = f"{HUNYUAN.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {HUNYUAN.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg.model_name,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else cfg.max_tokens,
        "temperature": temperature if temperature is not None else cfg.temperature,
        "stream": False,
    }

    resp = await client.post(
        url,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(AI_CALL_TIMEOUT, connect=10.0),
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
