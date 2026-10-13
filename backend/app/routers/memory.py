# ============================================================
# 文件：backend/app/routers/memory.py
# 说明：记忆库路由
#   GET    /api/memories          列表 + 关键词检索 + 类型/来源过滤 + 分页
#   GET    /api/memories/stats    概览统计（顶部卡片）
#   GET    /api/memories/{id}     详情
#   POST   /api/memories          手动写入一条记忆
#   DELETE /api/memories/{id}     删除一条
#   DELETE /api/memories          清空（可按 memory_type）
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.database import memory_repo
from app.middleware.error_handler import AppException
from app.models.schemas import ApiResponse

logger = logging.getLogger("app.routers.memory")

router = APIRouter(prefix="/api", tags=["memory"])


# 合法的记忆类型（与 memories 表 CHECK 约束保持一致）
_VALID_TYPES = ("judge", "discussion", "frame", "manual")


class MemoryCreateRequest(BaseModel):
    """手动写入记忆（前端「总结并记忆」之外的补充入口）。"""
    memory_type: str = Field("manual", description="judge/discussion/frame/manual")
    content: str = Field(..., min_length=1, max_length=20000)
    topic: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    judge_model: Optional[str] = None
    ai_id: Optional[str] = None
    display_name: Optional[str] = None


@router.get("/memories", response_model=ApiResponse)
async def list_memories(
    keyword: Optional[str] = Query(None, description="关键词，匹配正文/主题/发言者"),
    memory_type: Optional[str] = Query(None, description="judge/discussion/frame/manual"),
    source_type: Optional[str] = Query(None, description="来源类型，如 discussion"),
    source_id: Optional[int] = Query(None, description="来源 ID，如讨论 ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ApiResponse:
    if memory_type and memory_type not in _VALID_TYPES:
        raise AppException("INVALID_TYPE", f"memory_type 必须是 {_VALID_TYPES} 之一", http_status=400)

    data = await asyncio.to_thread(
        memory_repo.list_memories_sync,
        keyword=keyword,
        memory_type=memory_type,
        source_type=source_type,
        source_id=source_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    data["page"] = page
    data["page_size"] = page_size
    return ApiResponse(data=data)


@router.get("/memories/stats", response_model=ApiResponse)
async def memory_stats() -> ApiResponse:
    data = await asyncio.to_thread(memory_repo.stats_sync)
    return ApiResponse(data=data)


@router.get("/memories/{memory_id}", response_model=ApiResponse)
async def get_memory(memory_id: int) -> ApiResponse:
    item = await asyncio.to_thread(memory_repo.get_memory_sync, memory_id)
    if item is None:
        raise AppException("MEMORY_NOT_FOUND", "记忆不存在", http_status=404)
    return ApiResponse(data=item)


@router.post("/memories", response_model=ApiResponse)
async def create_memory(payload: MemoryCreateRequest) -> ApiResponse:
    if payload.memory_type not in _VALID_TYPES:
        raise AppException("INVALID_TYPE", f"memory_type 必须是 {_VALID_TYPES} 之一", http_status=400)

    memory_id = await asyncio.to_thread(
        memory_repo.insert_memory_sync,
        memory_type=payload.memory_type,
        content=payload.content,
        topic=payload.topic,
        source_type=payload.source_type,
        source_id=payload.source_id,
        judge_model=payload.judge_model,
        ai_id=payload.ai_id,
        display_name=payload.display_name,
    )
    return ApiResponse(data={"id": memory_id}, message="已写入记忆库")


@router.delete("/memories/{memory_id}", response_model=ApiResponse)
async def delete_memory(memory_id: int) -> ApiResponse:
    ok = await asyncio.to_thread(memory_repo.delete_memory_sync, memory_id)
    if not ok:
        raise AppException("MEMORY_NOT_FOUND", "记忆不存在", http_status=404)
    return ApiResponse(data={"id": memory_id}, message="已删除")


@router.delete("/memories", response_model=ApiResponse)
async def clear_memories(
    memory_type: Optional[str] = Query(None, description="不传则清空全部"),
) -> ApiResponse:
    if memory_type and memory_type not in _VALID_TYPES:
        raise AppException("INVALID_TYPE", f"memory_type 必须是 {_VALID_TYPES} 之一", http_status=400)

    count = await asyncio.to_thread(memory_repo.clear_memories_sync, memory_type)
    return ApiResponse(data={"deleted": count}, message=f"已清空 {count} 条记忆")
