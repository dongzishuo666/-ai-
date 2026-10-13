# ============================================================
# 文件：backend/app/routers/extensions.py
# 说明：扩展功能路由（扩展V4）
#   /api/chat/*        即时人机对话（模块A）
#   /api/teamup/*      智能组队（模块C）
#   /api/discussions/{id}/impeach|distill   弹劾/认知蒸馏（模块B/E）
#   /api/credit/*      用户信用治理（模块F）
#   /api/frames        认知框架查询（模块E）
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import AI_MODELS, SSE_HEARTBEAT_INTERVAL
from app.middleware.error_handler import AppException
from app.models.schemas import ApiResponse
from app.services.sse_manager import sse_manager
from app.database import discussion_repo
from app.database import extension_repo
from app.services import extension_service

logger = logging.getLogger("app.routers.extensions")

router = APIRouter(prefix="/api", tags=["extensions"])

# ---------- SSE 公共工具（与 discussions.py 保持一致） ----------

_bg_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _sse_generator(queue: asyncio.Queue, stream_id: str) -> AsyncIterator[str]:
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
            if event == "__close__":
                return
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if event == "done":
                return
    finally:
        sse_manager.unregister(stream_id)


# ============================================================
# 模块A：即时对话
# ============================================================

class ChatSessionCreateRequest(BaseModel):
    title: str = Field("新对话", max_length=100)
    mode: str = Field("quick", pattern="^(quick|serial|parallel)$")
    supervisor_enabled: bool = True
    memory_enabled: bool = True
    # 会话级模型：留空=按模式自动挑选；指定=与该模型单独对话（无附加提示词）
    ai_id: Optional[str] = None


class ChatMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class ChatSessionModelRequest(BaseModel):
    ai_id: Optional[str] = None


@router.post("/chat/sessions", response_model=ApiResponse)
async def create_chat_session(payload: ChatSessionCreateRequest) -> ApiResponse:
    ai_id = (payload.ai_id or "").strip() or None
    if ai_id and ai_id not in AI_MODELS:
        raise AppException(code="BAD_REQUEST", message=f"未知模型: {ai_id}",
                           http_status=400)
    session_id = await asyncio.to_thread(
        extension_repo.create_chat_session_sync,
        payload.title, payload.mode,
        payload.supervisor_enabled, payload.memory_enabled, ai_id)
    session = await asyncio.to_thread(
        extension_repo.get_chat_session_sync, session_id)
    return ApiResponse(data={"session": session})


@router.patch("/chat/sessions/{session_id}/model", response_model=ApiResponse)
async def update_chat_session_model(session_id: int,
                                    payload: ChatSessionModelRequest) -> ApiResponse:
    """切换会话所用的模型（传 null 回到按模式自动挑选）。"""
    ai_id = (payload.ai_id or "").strip() or None
    if ai_id and ai_id not in AI_MODELS:
        raise AppException(code="BAD_REQUEST", message=f"未知模型: {ai_id}",
                           http_status=400)
    exists = await asyncio.to_thread(
        extension_repo.get_chat_session_sync, session_id)
    if exists is None:
        raise AppException(code="NOT_FOUND", message="会话不存在",
                           http_status=404)
    await asyncio.to_thread(
        extension_repo.update_chat_session_ai_sync, session_id, ai_id)
    session = await asyncio.to_thread(
        extension_repo.get_chat_session_sync, session_id)
    return ApiResponse(data={"session": session})


@router.get("/chat/sessions", response_model=ApiResponse)
async def list_chat_sessions() -> ApiResponse:
    sessions = await asyncio.to_thread(extension_repo.list_chat_sessions_sync)
    return ApiResponse(data={"items": sessions, "total": len(sessions)})


@router.get("/chat/sessions/{session_id}", response_model=ApiResponse)
async def get_chat_session(session_id: int) -> ApiResponse:
    session = await asyncio.to_thread(
        extension_repo.get_chat_session_sync, session_id)
    if session is None:
        raise AppException(code="NOT_FOUND", message="会话不存在",
                           http_status=404)
    messages = await asyncio.to_thread(
        extension_repo.list_chat_messages_sync, session_id)
    return ApiResponse(data={"session": session, "messages": messages})


@router.delete("/chat/sessions/{session_id}", response_model=ApiResponse)
async def delete_chat_session(session_id: int) -> ApiResponse:
    ok = await asyncio.to_thread(
        extension_repo.delete_chat_session_sync, session_id)
    if not ok:
        raise AppException(code="NOT_FOUND", message="会话不存在",
                           http_status=404)
    return ApiResponse(data={"deleted": True})


@router.post("/chat/sessions/{session_id}/messages")
async def send_chat_message(session_id: int, payload: ChatMessageRequest):
    """
    发送消息并以 SSE 流式返回 AI 应答。
    事件：chat_user / chat_ai / chat_anomaly / chat_rewrite /
          chat_ai_error / danger_blocked / chat_done / done
    """
    session = await asyncio.to_thread(
        extension_repo.get_chat_session_sync, session_id)
    if session is None:
        raise AppException(code="NOT_FOUND", message="会话不存在",
                           http_status=404)

    stream_id = f"chat_{session_id}"
    queue = sse_manager.register(stream_id)
    _spawn(extension_service.chat_answer(
        session_id=session_id,
        question=payload.content,
        stream_id=stream_id,
    ))
    return StreamingResponse(
        _sse_generator(queue, stream_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ============================================================
# 模块C：智能组队
# ============================================================

class TeamupRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    multi: bool = False   # true=多AI协商，false=单AI快速


@router.post("/teamup/recommend", response_model=ApiResponse)
async def teamup_recommend(payload: TeamupRequest) -> ApiResponse:
    result = await extension_service.teamup_recommend(
        payload.topic, multi=payload.multi)
    return ApiResponse(data=result)


# ============================================================
# 模块B：弹劾
# ============================================================

class ImpeachRequest(BaseModel):
    initiator_ai_id: str = Field(..., min_length=1, max_length=50)
    target_role: str = Field(..., pattern="^(compliance|order|evaluation)$")
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/discussions/{discussion_id}/impeach", response_model=ApiResponse)
async def impeach_judge(discussion_id: int, payload: ImpeachRequest) -> ApiResponse:
    """发起弹劾投票（阻塞至投票完成，结果同步返回）。"""
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id)
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在",
                           http_status=404)

    stream_id = f"discussion_{discussion_id}"
    result = await extension_service.run_impeachment(
        discussion_id=discussion_id,
        initiator_ai_id=payload.initiator_ai_id,
        target_role=payload.target_role,
        reason=payload.reason,
        stream_id=stream_id,
    )
    if result.get("error"):
        raise AppException(code="VALIDATION_ERROR", message=result["error"],
                           http_status=400)
    return ApiResponse(data=result)


@router.get("/discussions/{discussion_id}/impeachments", response_model=ApiResponse)
async def list_impeachments(discussion_id: int) -> ApiResponse:
    items = await asyncio.to_thread(
        extension_repo.list_impeachments_sync, discussion_id)
    return ApiResponse(data={"items": items, "total": len(items)})


# ============================================================
# 模块E：认知蒸馏 / 框架
# ============================================================

@router.post("/discussions/{discussion_id}/distill")
async def distill_discussion(discussion_id: int):
    """认知蒸馏（SSE 流）：13部分总结 + 裁判总结 + 框架演化。"""
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id)
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在",
                           http_status=404)

    stream_id = f"distill_{discussion_id}"
    queue = sse_manager.register(stream_id)
    sse_manager.clear_cancel(stream_id)  # 清掉旧的取消标记

    async def run_and_close():
        try:
            await extension_service.distill_discussion(discussion_id, stream_id)
        finally:
            # 蒸馏流程不主动推 done，这里统一补发终结事件
            await sse_manager.publish(stream_id, "done", {})
            sse_manager.clear_cancel(stream_id)

    _spawn(run_and_close())
    return StreamingResponse(
        _sse_generator(queue, stream_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/discussions/{discussion_id}/judge-summary")
async def judge_summary_endpoint(
    discussion_id: int,
    judge_ai_id: Optional[str] = Query(
        None, description="指定独立裁判模型 ID，不传则按优先级自动选择"),
):
    """仅生成裁判总结（SSE 流），跳过单AI总结与框架演化。"""
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id)
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在",
                           http_status=404)

    stream_id = f"judge_{discussion_id}"
    queue = sse_manager.register(stream_id)
    sse_manager.clear_cancel(stream_id)

    async def run_and_close():
        try:
            await extension_service.judge_summary_only(
                discussion_id, stream_id, preferred_judge=judge_ai_id)
        finally:
            await sse_manager.publish(stream_id, "done", {})
            sse_manager.clear_cancel(stream_id)

    _spawn(run_and_close())
    return StreamingResponse(
        _sse_generator(queue, stream_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/discussions/{discussion_id}/distill/cancel")
async def cancel_distill(discussion_id: int):
    """请求取消正在进行的蒸馏（同时作用于完整蒸馏与仅裁判总结）。"""
    stream_id = f"distill_{discussion_id}"
    ok = sse_manager.cancel(stream_id)
    if not ok:
        # 也尝试取消 judge-only 流
        sse_manager.cancel(f"judge_{discussion_id}")
    return ApiResponse(data={"cancelled": True})


@router.get("/frames", response_model=ApiResponse)
async def list_frames(limit: int = Query(20, ge=1, le=100)) -> ApiResponse:
    items = await asyncio.to_thread(extension_repo.list_frames_sync, limit)
    return ApiResponse(data={"items": items, "total": len(items)})


# ============================================================
# 模块F：信用治理
# ============================================================

@router.get("/credit", response_model=ApiResponse)
async def get_credit() -> ApiResponse:
    credit = await asyncio.to_thread(extension_repo.get_credit_sync)
    behaviors = await asyncio.to_thread(
        extension_repo.list_recent_behavior_sync)
    return ApiResponse(data={"credit": credit, "recent_behaviors": behaviors})


class ThumbsDownRequest(BaseModel):
    target: str = Field(..., max_length=100)   # 点踩对象（消息/AI）
    detail: str = Field("", max_length=500)


@router.post("/credit/thumbs-down", response_model=ApiResponse)
async def thumbs_down(payload: ThumbsDownRequest) -> ApiResponse:
    """点踩。累计影响信用分；低于60触发AI圆桌会议。"""
    result = await extension_service.record_thumbs_down(
        payload.target, payload.detail)
    return ApiResponse(data=result)


class AppealRequest(BaseModel):
    content: str = Field(..., min_length=10, max_length=2000)


@router.post("/credit/appeal", response_model=ApiResponse)
async def appeal(payload: AppealRequest) -> ApiResponse:
    """用户申诉：记录后由圆桌会议复核。"""
    await asyncio.to_thread(
        extension_repo.log_user_action_sync, "default", "appeal",
        payload.content, credit_delta=0)
    roundtable = await extension_service.governance_roundtable(
        "用户提交申诉，复核近期行为记录")
    return ApiResponse(data=roundtable)
