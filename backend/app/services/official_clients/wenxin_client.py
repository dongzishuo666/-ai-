# ============================================================
# 文件：backend/app/services/official_clients/wenxin_client.py
# 说明：文心一言官方客户端。协议差异：
#   - 需要 API Key + Secret Key 换 access_token
#   - access_token 有效期 30 天，本模块内存缓存并提前 5 分钟刷新
#   - URL 形式：{base_url}/{model_name}?access_token={token}
#   - 响应为自有格式（result 字段），非 OpenAI choices 结构
# ============================================================

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import AI_CALL_TIMEOUT, WENXIN, ModelConfig


# access_token 内存缓存（单 worker，无需锁）
_TOKEN_CACHE: Dict[str, Any] = {
    "token": "",
    "expire_at": 0.0,      # 单调时钟，秒
}

# 提前刷新阈值（秒）
_REFRESH_AHEAD_SECONDS = 300


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """获取（或复用）文心 access_token。"""
    # 密钥自检：缺失时明确告知配置位置
    missing = []
    if not WENXIN.api_key:
        missing.append("WENXIN_API_KEY")
    if not WENXIN.secret_key:
        missing.append("WENXIN_SECRET_KEY")
    if missing:
        raise RuntimeError(
            f"文心 access_token 获取失败：{' / '.join(missing)} 未配置。"
            f"请在 backend/.env 中补齐后重启服务。"
        )

    now = time.monotonic()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["expire_at"] > now + _REFRESH_AHEAD_SECONDS:
        return _TOKEN_CACHE["token"]

    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": WENXIN.api_key,
        "client_secret": WENXIN.secret_key,
    }
    resp = await client.post(url, params=params, timeout=httpx.Timeout(15.0, connect=5.0))
    resp.raise_for_status()
    data = resp.json()

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"文心获取 access_token 失败: {data}")

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expire_at"] = now + int(data.get("expires_in", 2592000))
    return token


# 百度千帆 v3 版 API Key 前缀（形如 bce-v3/ALTAK-xxx/yyy）
_BCE_V3_PREFIX = "bce-v3/"
_BCE_V3_BASE_URL = "https://qianfan.baidubce.com/v2"


def is_bce_v3_key(api_key: str = "") -> bool:
    """判断是否为千帆 v3 版 API Key（无需再换 access_token）。"""
    return (api_key or WENXIN.api_key or "").strip().startswith(_BCE_V3_PREFIX)


async def _call_bce_v3(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    千帆 v3 API Key 直连（OpenAI 兼容协议）。
    认证方式：Authorization: Bearer bce-v3/ALTAK-xxx/yyy
    """
    base = WENXIN.base_url.strip() if WENXIN.base_url else ""
    if "qianfan.baidubce.com" not in base:
        base = _BCE_V3_BASE_URL
    url = f"{base.rstrip('/')}/chat/completions"

    payload: Dict[str, Any] = {
        "model": cfg.model_name,
        "messages": messages,
        "temperature": temperature if temperature is not None else cfg.temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    resp = await client.post(
        url,
        headers={
            "Authorization": f"Bearer {WENXIN.api_key.strip()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=httpx.Timeout(AI_CALL_TIMEOUT, connect=10.0),
    )

    if resp.status_code == 401:
        raise RuntimeError(
            "文心鉴权失败(401)：请确认 WENXIN_API_KEY 为完整的 "
            "bce-v3/ALTAK-xxx/yyy 形式（三段不要拆开），且该 Key 已开通对应模型。"
        )
    resp.raise_for_status()
    data = resp.json()

    if "error_code" in data or "error" in data:
        err = data.get("error") or {}
        raise RuntimeError(
            f"文心错误 [{data.get('error_code', err.get('code', 'unknown'))}]: "
            f"{data.get('error_msg', err.get('message', data))}"
        )

    choices = data.get("choices") or []
    content = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content") or choices[0].get("text") or ""

    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


async def call(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    client: httpx.AsyncClient,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    # v3 版 API Key：直接 Bearer 调用，不走 AK/SK 换 token 流程
    if is_bce_v3_key():
        return await _call_bce_v3(
            cfg, messages, client,
            max_tokens=max_tokens, temperature=temperature,
        )

    token = await _get_access_token(client)
    url = f"{WENXIN.base_url.rstrip('/')}/{cfg.model_name}?access_token={token}"

    payload = {
        "messages": messages,
        "temperature": temperature if temperature is not None else cfg.temperature,
    }
    # 文心对 max_tokens 支持有限，仅在明确给出时传递
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens

    resp = await client.post(
        url,
        json=payload,
        timeout=httpx.Timeout(AI_CALL_TIMEOUT, connect=10.0),
    )
    resp.raise_for_status()
    data = resp.json()

    # 文心错误码
    if "error_code" in data:
        raise RuntimeError(f"文心错误 [{data.get('error_code')}]: {data.get('error_msg')}")

    content = data.get("result", "")
    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
