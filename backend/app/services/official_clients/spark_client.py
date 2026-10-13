# ============================================================
# 文件：backend/app/services/official_clients/spark_client.py
# 说明：讯飞星辰 MaaS 官方渠道客户端。协议：OpenAI 兼容。
#       注意：X 系列（spark-x2.5-4b）为推理模型，会先输出 reasoning_content，
#       额度过小时正文会被思考过程吃光（实测 max_tokens=1024 返回空正文）。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import AI_CALL_TIMEOUT, SPARK, ModelConfig


async def call(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    url = f"{SPARK.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {SPARK.api_key}",
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

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
