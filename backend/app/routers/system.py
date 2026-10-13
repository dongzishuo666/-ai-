# ============================================================
# 文件：backend/app/routers/system.py
# 说明：公共系统接口。health / ai-status / config。
# ============================================================

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from app.config import (
    AI_CALL_TIMEOUT,
    AI_MAX_CONCURRENT,
    AI_MODELS,
    MAX_FILE_SIZE_MB,
)
from app.config.business import (
    DISCUSSION_CONFIG,
    DISCUSSION_PARTICIPANTS,
    GENRE_LABELS,
)
from app.database import service_run_repo
from app.database.connection import get_connection
from app.models.schemas import ApiResponse
from app.services.ai_health import probe_all_models
from app.services.ai_scheduler import get_active_calls

router = APIRouter(prefix="/api/system", tags=["system"])


# 进程启动时间（用于 uptime）
_PROCESS_START = time.monotonic()


# ============================================================
# 辅助
# ============================================================

def _read_rss_mb() -> int:
    """读取当前进程 RSS 内存占用（MB）。失败返回 0。"""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001
        pass
    return 0


def _count_active_docs() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM doc_tasks WHERE status = 'running'"
        ).fetchone()
    return int(row["c"]) if row else 0


def _count_active_discussions() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM discussions WHERE status IN ('running', 'paused')"
        ).fetchone()
    return int(row["c"]) if row else 0


def _count_active_articles() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM articles
            WHERE status IN ('outlining', 'outline_ready', 'writing', 'revising')
            """
        ).fetchone()
    return int(row["c"]) if row else 0


def _utc_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# GET /health
# ============================================================

@router.get("/health", response_model=ApiResponse)
async def health() -> ApiResponse:
    """健康检查：版本 / 内存 / 活跃任务 / 运行时长。"""
    # 三个计数并发执行（to_thread 避免阻塞事件循环）
    docs, discs, arts, service = await asyncio.gather(
        asyncio.to_thread(_count_active_docs),
        asyncio.to_thread(_count_active_discussions),
        asyncio.to_thread(_count_active_articles),
        asyncio.to_thread(service_run_repo.service_uptime_sync),
    )

    return ApiResponse(data={
        "status": "healthy",
        "version": "1.2.0",
        "uptime_seconds": int(time.monotonic() - _PROCESS_START),
        # 累计运行时长：把每次启动记成一段再相加，重启不清零
        "service": service,
        "memory_usage_mb": _read_rss_mb(),
        "memory_limit_mb": 2048,
        "active_tasks": {
            "doc_tasks": docs,
            "discussions": discs,
            "articles": arts,
        },
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


# ============================================================
# GET /ai-status
# ============================================================

@router.get("/ai-status", response_model=ApiResponse)
async def ai_status() -> ApiResponse:
    """
    AI 模型可用性探测。
    - 缓存 30 秒，避免频繁消耗额度
    - 8 个模型并发探活
    """
    result = await probe_all_models()

    # 格式化 last_check 时间
    last_check_iso = _utc_iso(result.get("checked_at"))
    for m in result["models"]:
        m["last_check"] = last_check_iso

    # 合并实时调用状态（不受探活缓存影响）
    active = get_active_calls()
    for m in result["models"]:
        cnt = active.get(m["ai_id"], 0)
        m["active_calls"] = cnt
        m["busy"] = cnt > 0

    return ApiResponse(data={
        "models": result["models"],
        "summary": result["summary"],
        "cached": result.get("cached", False),
    })


# ============================================================
# GET /config
# ============================================================

@router.get("/config", response_model=ApiResponse)
async def get_config() -> ApiResponse:
    """前端初始化配置。"""
    return ApiResponse(data={
        "features": {
            "doc_upload": {
                "enabled": True,
                "max_file_size_mb": MAX_FILE_SIZE_MB,
                "supported_formats": ["pdf", "docx", "txt", "md"],
            },
            "discussion": {
                "enabled": True,
                "max_rounds": DISCUSSION_CONFIG["max_rounds"],
                "max_participants": len(DISCUSSION_PARTICIPANTS),
                "supports_pause": True,
            },
            "article_gen": {
                "enabled": True,
                "max_word_count": 10000,
                "genres": list(GENRE_LABELS.keys()),
            },
        },
        "limits": {
            "concurrent_ai_calls": AI_MAX_CONCURRENT,
            "ai_call_timeout_seconds": AI_CALL_TIMEOUT,
            "max_concurrent_tasks": 1,
        },
    })


# ============================================================
# GET /token-stats —— Token 消耗面板
# ============================================================

# 与 init.sql 的 CHECK 约束保持一致，避免把任意字符串拼进 SQL
_TASK_TYPES = ("doc_process", "discussion", "article_gen", "judge", "system_check")


def _token_filters(days: int, task_type: Optional[str]) -> tuple:
    """
    拼出 ai_call_logs 的 WHERE 子句。

    days <= 0 表示不限时间；task_type 必须命中白名单，否则当「全部」处理
    （宁可多给数据，也不让非法参数变成慢查询或报错）。
    """
    clauses: List[str] = []
    params: List[Any] = []
    if days > 0:
        clauses.append(
            "created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)")
        params.append(f"-{int(days)} days")
    if task_type in _TASK_TYPES:
        clauses.append("task_type = ?")
        params.append(task_type)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _token_summary_sync(where: str, params: List[Any]) -> Dict[str, Any]:
    """全局汇总：调用数 / 成功失败 / 输入输出 token / 单次峰值。"""
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*)                                          AS calls,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN status = 'success' THEN 0 ELSE 1 END) AS failed,
                   COALESCE(SUM(prompt_tokens), 0)                   AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0)               AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0)                    AS total_tokens,
                   COALESCE(MAX(total_tokens), 0)                    AS max_tokens,
                   COALESCE(AVG(duration_ms), 0)                     AS avg_duration_ms
            FROM ai_call_logs{where}
            """,
            params,
        ).fetchone()
    return dict(row) if row else {}


def _token_by_model_sync(where: str, params: List[Any]) -> List[Dict[str, Any]]:
    """按模型累积：总量降序。"""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT ai_id,
                   COUNT(*)                                          AS calls,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN status = 'success' THEN 0 ELSE 1 END) AS failed,
                   COALESCE(SUM(prompt_tokens), 0)                   AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0)               AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0)                    AS total_tokens,
                   COALESCE(MAX(total_tokens), 0)                    AS max_tokens,
                   COALESCE(AVG(duration_ms), 0)                     AS avg_duration_ms
            FROM ai_call_logs{where}
            GROUP BY ai_id
            ORDER BY total_tokens DESC
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def _token_daily_sync(where: str, params: List[Any]) -> List[Dict[str, Any]]:
    """按天汇总，给趋势图用。"""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT substr(created_at, 1, 10)                         AS day,
                   COUNT(*)                                          AS calls,
                   COALESCE(SUM(total_tokens), 0)                    AS total_tokens
            FROM ai_call_logs{where}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def _token_recent_sync(where: str, params: List[Any],
                       limit: int) -> List[Dict[str, Any]]:
    """最近若干次调用的「单次消耗」，按时间正序返回（画图从左到右）。"""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, ai_id, task_type, prompt_tokens, completion_tokens,
                   total_tokens, duration_ms, status, created_at
            FROM ai_call_logs{where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _with_display(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """补上中文名与渠道，前端不用再维护一份映射。"""
    out = []
    for r in rows:
        cfg = AI_MODELS.get(r.get("ai_id"))
        r["display_name"] = cfg.display_name if cfg else r.get("ai_id")
        r["channel"] = cfg.channel if cfg else "-"
        out.append(r)
    return out


@router.get("/token-stats", response_model=ApiResponse)
async def token_stats(
    days: int = 7,
    task_type: Optional[str] = None,
    limit: int = 40,
) -> ApiResponse:
    """
    Token 消耗统计（面板数据源）。

    - days:      统计窗口天数，0 = 全部；默认 7
    - task_type: 任务类型过滤，不在白名单里则忽略（= 全部）
    - limit:     返回多少条「单次调用」明细，1~200，默认 40
    """
    days = max(0, min(int(days), 365))
    limit = max(1, min(int(limit), 200))

    where, params = _token_filters(days, task_type)
    summary, by_model, daily, recent = await asyncio.gather(
        asyncio.to_thread(_token_summary_sync, where, params),
        asyncio.to_thread(_token_by_model_sync, where, params),
        asyncio.to_thread(_token_daily_sync, where, params),
        asyncio.to_thread(_token_recent_sync, where, params, limit),
    )

    total = summary.get("total_tokens", 0) or 0
    calls = summary.get("calls", 0) or 0
    summary["avg_tokens"] = round(total / calls, 1) if calls else 0
    summary["fail_rate"] = round(
        (summary.get("failed", 0) or 0) / calls * 100, 1) if calls else 0
    summary["avg_duration_ms"] = round(summary.get("avg_duration_ms", 0) or 0)

    for m in by_model:
        c = m.get("calls", 0) or 0
        m["avg_tokens"] = round((m.get("total_tokens", 0) or 0) / c) if c else 0
        m["avg_duration_ms"] = round(m.get("avg_duration_ms", 0) or 0)
        m["share"] = round((m.get("total_tokens", 0) or 0) / total * 100, 1) if total else 0

    return ApiResponse(data={
        "range": {
            "days": days,
            "task_type": task_type if task_type in _TASK_TYPES else None,
            "task_types": list(_TASK_TYPES),
        },
        "summary": summary,
        "by_model": _with_display(by_model),
        "daily": daily,
        "recent": _with_display(recent),
    })
