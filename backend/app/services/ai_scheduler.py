# ============================================================
# 文件：backend/app/services/ai_scheduler.py
# 说明：AI 调度核心。
#   - 信号量 AI_MAX_CONCURRENT 控制并发
#   - 重试：AI_RETRY_DELAY_BASE × AI_RETRY_DELAY_MULTIPLIER ^ 尝试次数
#   - 降级：按 fallback 列表，受 AI_FALLBACK_ENABLED 控制
#   - 全失败：受 AI_SKIP_ON_FAILURE 决定 skipped 或抛错
#   - 熔断：连续 5 次失败标记 offline 60 秒
#   - 日志：每次调用写 ai_call_logs
#   - 分派：按 cfg.channel / cfg.ai_id 路由到对应客户端
# 约束：同步 sqlite3 写日志经 asyncio.to_thread 包装，避免阻塞事件循环。
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.config import (
    AI_FALLBACK_ENABLED,
    AI_MAX_CONCURRENT,
    AI_MODELS,
    AI_MODEL_MAX_CONCURRENT,
    AI_RATE_LIMIT_WAIT,
    AI_RETRY_DELAY_BASE,
    AI_RETRY_DELAY_MULTIPLIER,
    AI_SKIP_ON_FAILURE,
    MEMORY_LIMITS,
    ModelConfig,
    REASONING_MODELS,
    REASONING_MODEL_MIN_TOKENS,
)
from app.database.connection import get_transaction
from app.middleware.error_handler import AppException
from app.services import relay_client
from app.services.official_clients import (
    deepseek_client,
    hunyuan_client,
    kimi_client,
    spark_client,
    wenxin_client,
    zhipu_client,
)

logger = logging.getLogger("app.ai_scheduler")


# ============================================================
# 客户端注册表：ai_id -> client.call
#
# 以前是在 _call_single 里用 if/elif 按 ai_id 硬编码分派，新增一家模型
# 要同时改 settings / scheduler / official_clients 三处。现在只需要：
#   1) 实现一个 client 模块（暴露 async def call(cfg, messages, client, ...)）
#   2) 在这里加一行
# ============================================================

CLIENT_REGISTRY: Dict[str, Any] = {
    # ---------- 官方渠道 ----------
    "deepseek": deepseek_client.call,
    "zhipu":    zhipu_client.call,
    "wenxin":   wenxin_client.call,
    "kimi":     kimi_client.call,
    "spark":    spark_client.call,
    "hunyuan":  hunyuan_client.call,
    # ---------- 中转渠道（四模型统一走 relay） ----------
    "openai":   relay_client.call,
    "claude":   relay_client.call,
    "gemini":   relay_client.call,
    "grok":     relay_client.call,
}


def resolve_client(cfg: ModelConfig) -> Any:
    """
    选出这个模型该用的客户端入口。

    channel 为 relay 的一律走中转——中转上新模型不必再回来改注册表；
    其余按 ai_id 查注册表，查不到就把已注册的项写进报错，方便定位。
    """
    if cfg.channel == "relay":
        return relay_client.call

    fn = CLIENT_REGISTRY.get(cfg.ai_id)
    if fn is None:
        raise RuntimeError(
            "未知 AI 标识: %s（已注册: %s）"
            % (cfg.ai_id, ", ".join(sorted(CLIENT_REGISTRY)))
        )
    return fn


# ============================================================
# 常量
# ============================================================

_FAILURE_THRESHOLD = 5          # 连续失败阈值
_OFFLINE_SECONDS = 60           # 熔断时长
_REQUEST_PAYLOAD_MAX = 5000     # 请求体落库截断长度
_RESPONSE_PREVIEW_MAX = MEMORY_LIMITS["max_response_preview_chars"]  # 500


# ============================================================
# 结果数据结构
# ============================================================

@dataclass
class AICallResult:
    ai_id: str
    content: str = ""
    status: str = "success"            # success / failed / timeout / skipped
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_message: Optional[str] = None
    fallback_used: bool = False        # 是否经由降级链返回
    fallback_from: Optional[str] = None  # 原始主模型 ai_id（降级时记录）


# ============================================================
# 全局状态：信号量 + HTTP 客户端 + 熔断器
# ============================================================

_AI_SEMAPHORE = asyncio.Semaphore(AI_MAX_CONCURRENT)

# 单模型并发闸门：账户并发低的供应商（实测 Kimi 官方 = 1）在这里排队，
# 避免一场讨论里多个席位并行发请求互相撞 429。未配置的模型不受额外限制。
_MODEL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {
    ai_id: asyncio.Semaphore(max(1, limit))
    for ai_id, limit in AI_MODEL_MAX_CONCURRENT.items()
    if limit and limit > 0
}

_http_client: Optional[httpx.AsyncClient] = None


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    offline_until: float = 0.0     # 单调时钟，秒


_circuits: Dict[str, _CircuitState] = {}

# 实时调用计数：ai_id -> 正在执行中的请求数（用于前端"调用中/休息中"展示）
_active_calls: Dict[str, int] = {}


def get_active_calls() -> Dict[str, int]:
    """返回各 AI 当前正在执行的调用数（快照）。"""
    return {k: v for k, v in _active_calls.items() if v > 0}


# ============================================================
# HTTP 客户端生命周期
# ============================================================

async def get_http_client() -> httpx.AsyncClient:
    """懒加载全局 httpx.AsyncClient（连接池复用）。"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
        )
    return _http_client


async def close_http_client() -> None:
    """应用关闭时释放连接池。"""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# ============================================================
# 熔断器
# ============================================================

def _is_circuit_open(ai_id: str) -> bool:
    state = _circuits.get(ai_id)
    if state is None:
        return False
    return state.offline_until > time.monotonic()


def _record_failure(ai_id: str) -> None:
    state = _circuits.setdefault(ai_id, _CircuitState())
    state.consecutive_failures += 1
    if state.consecutive_failures >= _FAILURE_THRESHOLD:
        state.offline_until = time.monotonic() + _OFFLINE_SECONDS
        logger.warning("AI [%s] 连续失败 %d 次，熔断 %d 秒",
                       ai_id, state.consecutive_failures, _OFFLINE_SECONDS)


def _record_success(ai_id: str) -> None:
    if ai_id in _circuits:
        _circuits[ai_id] = _CircuitState()


def _is_retryable(result: "AICallResult") -> bool:
    """
    判断失败是否值得重试。

    429 限流、401/403/400 等 4xx 是确定性错误：重试不仅无益，还会
    （a）把单次调用阻塞时间放大到 AI_CALL_TIMEOUT × (retry_count+1)，
    （b）对已经限流的供应商继续加压，反而更难恢复。
    这类错误应立即跳出重试，交给降级链处理。
    """
    if result.status == "timeout":
        return True  # 网络抖动值得重试

    msg = result.error_message or ""
    m = re.search(r"HTTP\s+(\d{3})", msg)
    if m:
        code = int(m.group(1))
        # 408 是请求超时，可重试；其余 4xx 一律不重试
        if 400 <= code < 500 and code != 408:
            return False
    if "Too Many Requests" in msg or "rate limit" in msg.lower():
        return False
    return True


def _is_rate_limited(msg: str) -> bool:
    """
    判断是否撞到供应商限流（429）。

    这类错误是瞬时的——账户并发释放后就能成功，和 401/403/400 那种
    确定性错误不同，值得退避后重试一次。
    """
    if not msg:
        return False
    if "429" in msg or "Too Many Requests" in msg:
        return True
    low = msg.lower()
    return "rate limit" in low or "rate_limit" in low or "concurrency" in low


# ============================================================
# 日志落库（to_thread 包装）
# ============================================================

def _write_log_sync(
    cfg: ModelConfig,
    task_type: str,
    task_id: Optional[int],
    result: AICallResult,
    request_payload: str,
) -> None:
    sql = """
        INSERT INTO ai_call_logs
            (ai_id, model, channel, task_type, task_id,
             prompt_tokens, completion_tokens, total_tokens,
             duration_ms, status, error_message,
             request_payload, response_preview)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    preview = (result.content or "")[:_RESPONSE_PREVIEW_MAX]
    with get_transaction() as conn:
        conn.execute(
            sql,
            (
                cfg.ai_id,
                cfg.model_name,
                cfg.channel,
                task_type,
                task_id,
                result.prompt_tokens,
                result.completion_tokens,
                result.total_tokens,
                result.duration_ms,
                result.status,
                result.error_message,
                request_payload,
                preview,
            ),
        )


async def _write_log(
    cfg: ModelConfig,
    task_type: str,
    task_id: Optional[int],
    result: AICallResult,
    messages: List[Dict[str, str]],
) -> None:
    """异步写日志。失败不阻塞业务，仅记录告警。"""
    try:
        payload_str = json.dumps(
            {"model": cfg.model_name, "messages": messages},
            ensure_ascii=False,
        )
        if len(payload_str) > _REQUEST_PAYLOAD_MAX:
            payload_str = payload_str[:_REQUEST_PAYLOAD_MAX] + "...[truncated]"
        await asyncio.to_thread(
            _write_log_sync, cfg, task_type, task_id, result, payload_str
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("写 ai_call_logs 失败: %s", e)


# ============================================================
# 单次调用（不含重试）
# ============================================================

async def _call_single(
    cfg: ModelConfig,
    messages: List[Dict[str, str]],
    max_tokens: Optional[int],
    temperature: Optional[float],
) -> AICallResult:
    start = time.monotonic()
    _active_calls[cfg.ai_id] = _active_calls.get(cfg.ai_id, 0) + 1
    try:
        client = await get_http_client()

        # 按注册表分派：新增模型 = 加一行 CLIENT_REGISTRY，不用再改这里
        fn = resolve_client(cfg)
        raw = await fn(
            cfg, messages, client,
            max_tokens=max_tokens, temperature=temperature,
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        return AICallResult(
            ai_id=cfg.ai_id,
            content=raw.get("content", ""),
            status="success",
            duration_ms=duration_ms,
            prompt_tokens=raw.get("prompt_tokens", 0),
            completion_tokens=raw.get("completion_tokens", 0),
            total_tokens=raw.get("total_tokens", 0),
        )

    except httpx.TimeoutException as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return AICallResult(
            ai_id=cfg.ai_id, content="", status="timeout",
            duration_ms=duration_ms, error_message=f"超时: {e}",
        )
    except httpx.HTTPStatusError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:  # noqa: BLE001
            pass
        return AICallResult(
            ai_id=cfg.ai_id, content="", status="failed",
            duration_ms=duration_ms,
            error_message=f"HTTP {e.response.status_code}: {body}",
        )
    except Exception as e:  # noqa: BLE001
        duration_ms = int((time.monotonic() - start) * 1000)
        return AICallResult(
            ai_id=cfg.ai_id, content="", status="failed",
            duration_ms=duration_ms, error_message=str(e),
        )
    finally:
        _active_calls[cfg.ai_id] = max(0, _active_calls.get(cfg.ai_id, 1) - 1)


# ============================================================
# 带重试的调用（单模型）
# ============================================================

async def _try_call_with_retry(
    ai_id: str,
    messages: List[Dict[str, str]],
    *,
    task_type: str,
    task_id: Optional[int],
    max_tokens: Optional[int],
    temperature: Optional[float],
) -> AICallResult:
    # 熔断检查
    if _is_circuit_open(ai_id):
        logger.info("AI [%s] 熔断中，跳过", ai_id)
        return AICallResult(
            ai_id=ai_id, status="skipped",
            error_message="熔断中（连续失败已被标记 offline）",
        )

    cfg = AI_MODELS.get(ai_id)
    if cfg is None:
        return AICallResult(
            ai_id=ai_id, status="failed",
            error_message=f"未在 AI_MODELS 中注册: {ai_id}",
        )

    max_attempts = cfg.retry_count + 1
    last_result: Optional[AICallResult] = None

    for attempt in range(max_attempts):
        if attempt > 0:
            delay = AI_RETRY_DELAY_BASE * (AI_RETRY_DELAY_MULTIPLIER ** (attempt - 1))
            await asyncio.sleep(delay)

        # 先过本模型闸门，再过全局闸门：
        # 反过来会占着全局并发位干等，把其它模型一起饿死。
        model_sem = _MODEL_SEMAPHORES.get(cfg.ai_id)
        if model_sem is not None:
            await model_sem.acquire()
        try:
            async with _AI_SEMAPHORE:
                result = await _call_single(cfg, messages, max_tokens, temperature)
        finally:
            if model_sem is not None:
                model_sem.release()

        await _write_log(cfg, task_type, task_id, result, messages)
        last_result = result

        if result.status == "success":
            _record_success(ai_id)
            return result
        _record_failure(ai_id)

        if not _is_retryable(result):
            # 限流（429）是瞬时的，退避后值得再试一次；其余 4xx 直接交给降级链
            if (_is_rate_limited(result.error_message or "")
                    and attempt < max_attempts - 1):
                logger.info(
                    "AI [%s] 撞限流，%.1fs 后重试（第 %d/%d 次）",
                    ai_id, AI_RATE_LIMIT_WAIT, attempt + 1, max_attempts,
                )
                await asyncio.sleep(AI_RATE_LIMIT_WAIT)
                continue
            logger.info("AI [%s] 命中不可重试错误，跳过剩余重试: %s",
                        ai_id, result.error_message)
            break

    # 所有重试失败，返回最后一次
    assert last_result is not None
    return last_result


# ============================================================
# 对外主入口
# ============================================================

async def call_ai(
    ai_id: str,
    messages: List[Dict[str, str]],
    *,
    task_type: str,
    task_id: Optional[int] = None,
    fallback: Optional[List[str]] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> AICallResult:
    """
    调用指定 AI，含重试/降级/熔断/日志。

    参数：
        ai_id      主模型 ID（如 'claude'）
        messages   OpenAI 格式消息列表
        task_type  'doc_process' / 'discussion' / 'article_gen' / 'judge' / 'system_check'
        task_id    关联业务任务 ID（可空）
        fallback   降级链（按顺序尝试）
        max_tokens / temperature  覆盖默认值

    返回：
        AICallResult
        - status='success'  → content 有效
        - status='skipped'  → 主模型 + 降级链全部失败，且 AI_SKIP_ON_FAILURE=True
        - status='failed'   → 失败且 AI_SKIP_ON_FAILURE=False 时抛出 AppException

    异常：
        AppException(AI_UNAVAILABLE, 503)  当 AI_SKIP_ON_FAILURE=False 且全部失败
    """
    # 0. 推理模型额度兜底：这类模型先把 token 花在 reasoning 上，
    #    调用处给的额度过小（如对话 128、组队 512）时正文会直接返回空串。
    #    统一在这里抬到下限，调用点不必各自记得处理。
    if (ai_id in REASONING_MODELS and max_tokens is not None
            and max_tokens < REASONING_MODEL_MIN_TOKENS):
        logger.debug(
            "AI [%s] 为推理模型，max_tokens %d 抬升至 %d",
            ai_id, max_tokens, REASONING_MODEL_MIN_TOKENS,
        )
        max_tokens = REASONING_MODEL_MIN_TOKENS

    # 1. 主模型
    result = await _try_call_with_retry(
        ai_id, messages,
        task_type=task_type, task_id=task_id,
        max_tokens=max_tokens, temperature=temperature,
    )
    if result.status == "success":
        return result

    # 2. 降级链
    if AI_FALLBACK_ENABLED and fallback:
        for fb_id in fallback:
            if fb_id == ai_id:
                continue
            fb_result = await _try_call_with_retry(
                fb_id, messages,
                task_type=task_type, task_id=task_id,
                max_tokens=max_tokens, temperature=temperature,
            )
            if fb_result.status == "success":
                fb_result.fallback_used = True
                fb_result.fallback_from = ai_id
                logger.info("AI [%s] 失败，已降级至 [%s]", ai_id, fb_id)
                return fb_result

    # 3. 全失败
    if AI_SKIP_ON_FAILURE:
        result.status = "skipped"
        result.error_message = (result.error_message or "") + "（已跳过，继续流程）"
        return result

    raise AppException(
        code="AI_UNAVAILABLE",
        message="AI 服务暂时不可用，请稍后重试",
        http_status=503,
        detail=f"主模型 {ai_id} 与降级链全部失败",
    )
