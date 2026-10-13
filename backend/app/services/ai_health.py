# ============================================================
# 文件：backend/app/services/ai_health.py
# 说明：8 个 AI 的可用性探活。
# 策略：
#   - 复用现有客户端，通过 asyncio.wait_for 施加 8s 硬超时（避免 45s AI_CALL_TIMEOUT 拖慢接口）
#   - 并发探活（asyncio.gather）
#   - 结果缓存 30 秒（避免频繁消耗 API 额度）
#   - 不写 ai_call_logs（探活属 system_check，日志噪音大）
# ============================================================

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

import httpx

from app.config import AI_MODELS, CHANNEL_CONNECTIONS
from app.services.ai_scheduler import get_http_client
from app.services.official_clients import (
    deepseek_client,
    hunyuan_client,
    kimi_client,
    spark_client,
    wenxin_client,
    zhipu_client,
)
from app.services import relay_client

logger = logging.getLogger("app.ai_health")


# 探活参数
_PROBE_TIMEOUT = 8.0                  # 单模型硬超时（秒）
_DEGRADED_THRESHOLD_MS = 3000         # 延迟超此值标记 degraded
_CACHE_TTL = 30                       # 缓存有效期（秒）

# 探活请求（最小化 token 消耗）
_PROBE_MESSAGES = [{"role": "user", "content": "ping"}]

# 缓存
_cache: Dict[str, Any] = {"timestamp": 0.0, "data": None}


# ============================================================
# 单模型探活
# ============================================================

async def _probe_one(ai_id: str) -> Dict[str, Any]:
    """探活单个模型。返回 {status, latency_ms, error}。"""
    cfg = AI_MODELS.get(ai_id)
    if cfg is None:
        return {"ai_id": ai_id, "status": "offline", "latency_ms": None,
                "error": "未在 AI_MODELS 注册"}

    # 密钥预检（避免无意义请求）
    if cfg.channel == "relay" and not CHANNEL_CONNECTIONS["relay"].api_key:
        return {"ai_id": ai_id, "status": "offline", "latency_ms": None,
                "error": "RELAY_API_KEY 未配置"}
    if cfg.channel == "official":
        conn = CHANNEL_CONNECTIONS.get(cfg.ai_id)
        if conn is None or not conn.api_key:
            return {"ai_id": ai_id, "status": "offline", "latency_ms": None,
                    "error": f"{cfg.ai_id.upper()}_API_KEY 未配置"}
        # 文心：bce-v3 版密钥自带签名，无需 Secret Key
        if cfg.ai_id == "wenxin" \
                and not (conn.api_key or "").strip().startswith("bce-v3/") \
                and not getattr(conn, "secret_key", ""):
            return {"ai_id": ai_id, "status": "offline", "latency_ms": None,
                    "error": "WENXIN_SECRET_KEY 未配置"}

    start = time.monotonic()
    try:
        client = await get_http_client()

        async def _do_call():
            if cfg.channel == "relay":
                return await relay_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            if cfg.ai_id == "deepseek":
                return await deepseek_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            if cfg.ai_id == "zhipu":
                return await zhipu_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            if cfg.ai_id == "wenxin":
                return await wenxin_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            if cfg.ai_id == "kimi":
                return await kimi_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            if cfg.ai_id == "spark":
                return await spark_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            if cfg.ai_id == "hunyuan":
                return await hunyuan_client.call(
                    cfg, _PROBE_MESSAGES, client, max_tokens=5, temperature=0.0,
                )
            raise RuntimeError(f"未分派的 ai_id: {cfg.ai_id}")

        await asyncio.wait_for(_do_call(), timeout=_PROBE_TIMEOUT)
        latency_ms = int((time.monotonic() - start) * 1000)

        if latency_ms > _DEGRADED_THRESHOLD_MS:
            return {"ai_id": ai_id, "status": "degraded",
                    "latency_ms": latency_ms, "error": None}
        return {"ai_id": ai_id, "status": "online",
                "latency_ms": latency_ms, "error": None}

    except asyncio.TimeoutError:
        return {"ai_id": ai_id, "status": "offline",
                "latency_ms": None, "error": "timeout"}
    except httpx.HTTPStatusError as e:
        return {"ai_id": ai_id, "status": "offline", "latency_ms": None,
                "error": f"HTTP {e.response.status_code}"}
    except Exception as e:  # noqa: BLE001
        logger.warning("探活 [%s] 异常: %s", ai_id, e)
        return {"ai_id": ai_id, "status": "offline",
                "latency_ms": None, "error": str(e)[:120]}


# ============================================================
# 对外主入口
# ============================================================

async def probe_all_models(force: bool = False) -> Dict[str, Any]:
    """
    并发探活全部 8 个模型（缓存 30 秒）。
    返回：{models: [...], summary: {...}, cached: bool, checked_at: float}
    """
    now = time.monotonic()
    if (
        not force
        and _cache["data"] is not None
        and (now - _cache["timestamp"]) < _CACHE_TTL
    ):
        data = dict(_cache["data"])
        data["cached"] = True
        return data

    # 并发探活
    tasks = [asyncio.create_task(_probe_one(ai_id)) for ai_id in AI_MODELS.keys()]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # 汇总
    summary = {"total": len(results), "online": 0, "degraded": 0, "offline": 0}
    checked_at = time.time()
    models_payload: List[Dict[str, Any]] = []

    for r in results:
        ai_id = r["ai_id"]
        cfg = AI_MODELS[ai_id]
        status = r["status"]
        if status in summary:
            summary[status] += 1
        item = {
            "ai_id": ai_id,
            "display_name": cfg.display_name,
            "channel": cfg.channel,
            "status": status,
            "latency_ms": r["latency_ms"],
            "last_check": None,   # 由 router 层格式化
        }
        if r.get("error"):
            item["error"] = r["error"]
        models_payload.append(item)

    result = {
        "models": models_payload,
        "summary": summary,
        "cached": False,
        "checked_at": checked_at,
    }
    _cache["timestamp"] = now
    _cache["data"] = result
    return result
