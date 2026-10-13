"""系统信息路由：AI 列表、健康检查、运行模式。"""
from __future__ import annotations

from fastapi import APIRouter

from app.ai.registry import ALL_IDS, get_profile, is_ready, public_ai_list
from app.config import settings

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": settings.version}


@router.get("/config")
async def config() -> dict:
    ready_count = sum(1 for ai_id in ALL_IDS if is_ready(get_profile(ai_id)))
    return {
        "appName": settings.app_name,
        "version": settings.version,
        "mockMode": settings.force_mock or ready_count == 0,
        "readyCount": ready_count,
        "totalCount": len(ALL_IDS),
        "maxUploadMb": settings.max_upload_mb,
    }


@router.get("/ai-list")
async def ai_list() -> dict:
    return {"items": public_ai_list()}
