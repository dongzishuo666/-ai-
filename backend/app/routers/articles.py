# ============================================================
# 文件：backend/app/routers/articles.py
# 说明：功能三（AI 三级文章生成）路由。
# 状态：批次1 → 实现 POST /create（仅入库，不启动任务，遵循裁定三）
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from fastapi import APIRouter

from app.database import article_repo
from app.middleware.error_handler import AppException
from app.models.schemas import (
    ApiResponse,
    ArticleCreateData,
    ArticleCreateRequest,
)

logger = logging.getLogger("app.routers.articles")

router = APIRouter(prefix="/api/articles", tags=["articles"])

# 可自选模型的角色（功能三三级写作）
_ARTICLE_ROLE_KEYS = ("outliner", "writer", "reviewer")


def _roles_query(roles: Optional[Dict[str, str]], keys) -> str:
    """把自选模型拼成 stream_url 查询串（不落库，随 SSE 连接传入流水线）。"""
    if not roles:
        return ""
    parts = [
        f"{k}={roles[k]}" for k in keys
        if isinstance(roles.get(k), str) and roles[k].strip()
    ]
    return "?" + "&".join(parts) if parts else ""


@router.post("/create", response_model=ApiResponse, status_code=200)
async def create_article(payload: ArticleCreateRequest) -> ApiResponse:
    """
    创建文章任务。
    - 仅入库，状态为 'created'
    - 不启动流水线（由后续 GET /{id}/stream 触发）
    - 若已有非终结态文章，返回 409 TASK_BUSY
    """
    # 1. 并发防护：同一时刻只允许 1 个文章任务处于非终结态
    active = await asyncio.to_thread(article_repo.count_active_articles_sync)
    if active > 0:
        raise AppException(
            code="TASK_BUSY",
            message="已有文章任务正在运行中，请等待完成后再提交",
            http_status=409,
        )

    # 2. 入库
    row = await asyncio.to_thread(
        article_repo.insert_article_sync,
        topic=payload.topic,
        genre=payload.genre,
        target_word_count=payload.target_word_count,
        style=payload.style,
        audience=payload.audience,
        additional_requirements=payload.additional_requirements,
    )

    # 3. 组装响应（stream_url 供前端建立 SSE 连接）
    article_id = row["article_id"]
    data = ArticleCreateData(
        article_id=article_id,
        topic=payload.topic,
        status=row["status"],
        stream_url=(
            f"/api/articles/{article_id}/stream"
            + _roles_query(payload.roles, _ARTICLE_ROLE_KEYS)
        ),
        created_at=row["created_at"],
    )
    logger.info("文章任务创建成功：id=%s topic=%s", article_id, payload.topic)
    return ApiResponse(data=data.model_dump())


# ============================================================
# 追加：批次2 · SSE 流
# ============================================================

import json
from typing import AsyncIterator

from fastapi.responses import StreamingResponse

from app.config import SSE_HEARTBEAT_INTERVAL
from app.database import article_repo
from app.services.article_pipeline import run_article_pipeline
from app.services.sse_manager import sse_manager


# 后台任务引用集（防止 GC）
_bg_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ---------- SSE 响应头（禁止 Nginx 缓冲） ----------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------- 生成器 ----------

async def _sse_generator(queue: asyncio.Queue, stream_id: str) -> AsyncIterator[str]:
    """
    从队列消费事件，格式化为 SSE 帧。
    - 心跳：每 SSE_HEARTBEAT_INTERVAL 秒发一次 ': keepalive'
    - done 事件后关闭
    - 客户端断开时（CancelledError）注销连接
    """
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=SSE_HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            event = item.get("event")
            data = item.get("data", {})

            # 内部关闭信号
            if event == "__close__":
                return

            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            if event == "done":
                return
    finally:
        sse_manager.unregister(stream_id)


async def _sse_terminal(article_id: int, status: str) -> AsyncIterator[str]:
    """
    终态连接（任务已完成/失败）：
    读取最终数据后立即推 final_result / error + done，然后关闭。
    """
    try:
        if status == "done":
            stages = await asyncio.to_thread(article_repo.get_stages_sync, article_id)
            revision = next((s for s in stages if s["stage"] == "revision"), None)
            article = await asyncio.to_thread(article_repo.get_article_sync, article_id)
            payload = {
                "article": (revision or {}).get("output_content", "") or "",
                "word_count": (article or {}).get("final_word_count", 0),
                "total_duration_ms": (article or {}).get("total_duration_ms", 0),
                "revision_notes": (revision or {}).get("revision_notes", "") or "",
            }
            yield f"event: final_result\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        else:
            article = await asyncio.to_thread(article_repo.get_article_sync, article_id)
            msg = (article or {}).get("error_message", "") or "任务已失败"
            yield f"event: error\ndata: {json.dumps({'message': msg, 'code': 'INTERNAL_ERROR'}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
    finally:
        pass


# ---------- 端点 ----------

@router.get("/{article_id}/stream")
async def stream_article(
    article_id: int,
    outliner: Optional[str] = None,
    writer: Optional[str] = None,
    reviewer: Optional[str] = None,
):
    """
    SSE 进度流。裁定三：连接后启动任务。
    可选查询参数 outliner / writer / reviewer：指定各阶段使用的模型。
    - created       → 注册 → 切状态 outlining → 启动流水线
    - 非终结态      → 重连（不重启任务）
    - done/failed   → 立即推终态后关闭
    - 已有活跃连接  → 409
    """
    article = await asyncio.to_thread(article_repo.get_article_sync, article_id)
    if article is None:
        raise AppException(
            code="NOT_FOUND", message="文章不存在", http_status=404
        )

    status = article["status"]

    # 终态直接返回
    if status in ("done", "failed"):
        return StreamingResponse(
            _sse_terminal(article_id, status),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    stream_id = f"article_{article_id}"

    # 重复连接防护
    if sse_manager.is_registered(stream_id):
        raise AppException(
            code="TASK_BUSY",
            message="该任务已有活跃的 SSE 连接",
            http_status=409,
        )

    # 注册
    queue = sse_manager.register(stream_id)

    # 首次连接：原子切换状态并启动流水线
    if status == "created":
        ok = await asyncio.to_thread(
            article_repo.update_article_status_sync,
            article_id, "outlining", expected_status="created",
        )
        if not ok:
            sse_manager.unregister(stream_id)
            raise AppException(
                code="TASK_BUSY",
                message="任务已被其他连接启动",
                http_status=409,
            )
        roles = {
            "outliner": outliner, "writer": writer, "reviewer": reviewer
        }
        _spawn(run_article_pipeline(article_id, roles or None))

    return StreamingResponse(
        _sse_generator(queue, stream_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


from fastapi import APIRouter, Query


# ============================================================
# 追加：批次3 · 详情 / 列表 / 删除
# ============================================================

# ---------- 辅助：组装 stages dict ----------

def _build_stages_dict(stage_rows: list) -> dict:
    """
    将 repo 返回的 stages 列表转为蓝图 3.3 约定的 dict 结构。
    固定返回 outline / draft / revision 三个 key（缺失时用 None）。
    """
    result = {"outline": None, "draft": None, "revision": None}
    for s in stage_rows:
        key = s["stage"]
        if key not in result:
            continue
        item = {
            "status": s["status"],
            "model": s["model"],
            "content": s["output_content"],
            "duration_ms": s["duration_ms"],
        }
        # draft / revision 含字数
        if key in ("draft", "revision"):
            item["word_count"] = s["word_count"]
        # revision 含修改说明
        if key == "revision":
            item["revision_notes"] = s["revision_notes"]
        # 失败时附错误信息
        if s["status"] == "failed":
            item["error_message"] = s["error_message"]
        result[key] = item
    return result


# ---------- 详情 ----------

@router.get("/{article_id}", response_model=ApiResponse)
async def get_article_detail(article_id: int) -> ApiResponse:
    """
    获取文章详情（含三阶段完整内容）。
    不存在返回 404 NOT_FOUND。
    """
    article = await asyncio.to_thread(article_repo.get_article_sync, article_id)
    if article is None:
        raise AppException(
            code="NOT_FOUND",
            message="文章不存在",
            http_status=404,
        )

    stages = await asyncio.to_thread(article_repo.get_stages_sync, article_id)
    stages_dict = _build_stages_dict(stages)

    data = {
        "article_id": article["article_id"],
        "topic": article["topic"],
        "genre": article["genre"],
        "target_word_count": article["target_word_count"],
        "style": article["style"],
        "audience": article["audience"],
        "additional_requirements": article["additional_requirements"],
        "status": article["status"],
        "stages": stages_dict,
        "final_word_count": article["final_word_count"],
        "total_duration_ms": article["total_duration_ms"],
        "error_message": article["error_message"],
        "created_at": article["created_at"],
        "completed_at": article["completed_at"],
    }
    return ApiResponse(data=data)


# ---------- 列表 ----------

@router.get("", response_model=ApiResponse)
async def list_articles(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(20, ge=1, le=50, description="每页条数，最大50"),
) -> ApiResponse:
    """
    文章历史列表（分页）。
    默认 page=1、page_size=20；page_size 上限 50（与蓝图 0.8 一致）。
    """
    result = await asyncio.to_thread(
        article_repo.list_articles_sync, page, page_size
    )
    return ApiResponse(data=result)


# ---------- 删除 ----------

@router.delete("/{article_id}", response_model=ApiResponse)
async def delete_article(article_id: int) -> ApiResponse:
    """
    删除文章（含 stages，靠 CASCADE）。
    约束：仅允许 done / failed；其他状态返回 409 TASK_BUSY。
    """
    article = await asyncio.to_thread(article_repo.get_article_sync, article_id)
    if article is None:
        raise AppException(
            code="NOT_FOUND",
            message="文章不存在",
            http_status=404,
        )

    if article["status"] not in ("done", "failed"):
        raise AppException(
            code="TASK_BUSY",
            message=f"文章状态为 {article['status']}，仅允许删除 done/failed 状态的文章",
            http_status=409,
        )

    deleted = await asyncio.to_thread(article_repo.delete_article_sync, article_id)
    if not deleted:
        # 理论上不会触发（前面已校验状态），保留兜底
        raise AppException(
            code="TASK_BUSY",
            message="删除失败，文章状态可能已变更",
            http_status=409,
        )

    logger.info("文章已删除：id=%s", article_id)
    return ApiResponse(data={"article_id": article_id, "deleted": True})
