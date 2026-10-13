# ============================================================
# 文件：backend/app/routers/scores.py
# 说明：统一评分榜单查询。三个功能的最终分都落在 ai_score_records：
#         feature=discussion → 讨论终局评价裁判的最终分（最终积分榜）
#         feature=article    → 文章流水线质量检测员打分
#         feature=doc        → 文档流水线质量检测员打分
#       GET /api/scores/task/{feature}/{task_id}   某场任务的榜
#       GET /api/scores/ranking                    历史总榜（每个AI的平时分）
#       GET /api/scores/ai/{ai_id}                 单个AI的近期得分
#       GET /api/scores/recent                     最近完成的任务（可切榜查看）
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Path, Query

from app.database import score_repo
from app.models.schemas import ApiResponse

logger = logging.getLogger("app.routers.scores")

router = APIRouter(prefix="/api/scores", tags=["评分榜单"])

_FEATURE_DESC = "discussion（讨论）/ article（文章）/ doc（文档）"


@router.get("/task/{feature}/{task_id}", response_model=ApiResponse)
async def get_task_scores(
    feature: str = Path(..., description=_FEATURE_DESC),
    task_id: int = Path(..., description="讨论 / 文章 / 文档任务 id"),
) -> ApiResponse:
    """某场任务的最终榜（讨论终榜 / 质检榜）。"""
    scores, meta = await asyncio.gather(
        asyncio.to_thread(score_repo.get_task_scores_sync, feature, task_id),
        asyncio.to_thread(score_repo.get_task_meta_sync, feature, task_id),
    )
    return ApiResponse(data={
        "feature": feature,
        "task_id": task_id,
        "scores": scores,
        "overall": meta.get("overall"),
        "summary": meta.get("summary", ""),
    })


@router.get("/ranking", response_model=ApiResponse)
async def get_ranking(
    feature: Optional[str] = Query(
        None, description="按功能过滤；留空=跨功能汇总"),
    limit: int = Query(50, ge=1, le=200),
) -> ApiResponse:
    """历史总榜：每个 AI 的平时分（平均分 / 场次 / 最高最低）。"""
    ranking = await asyncio.to_thread(
        score_repo.get_ai_ranking_sync, feature, limit)
    return ApiResponse(data={
        "feature": feature or "all",
        "ranking": ranking,
    })


@router.get("/ai/{ai_id}", response_model=ApiResponse)
async def get_ai_scores(
    ai_id: str = Path(..., description="模型 id，如 deepseek"),
    feature: Optional[str] = Query(None, description=_FEATURE_DESC),
    limit: int = Query(10, ge=1, le=50),
) -> ApiResponse:
    """单个 AI 的近期得分明细。"""
    history = await asyncio.to_thread(
        score_repo.get_ai_history_sync, ai_id, feature, limit)
    return ApiResponse(data={"ai_id": ai_id, "history": history})


@router.get("/recent", response_model=ApiResponse)
async def get_recent_tasks(
    feature: Optional[str] = Query(None, description=_FEATURE_DESC),
    limit: int = Query(10, ge=1, le=50),
) -> ApiResponse:
    """最近完成的任务列表（榜单弹窗里可切换查看某一场的榜）。"""
    tasks = await asyncio.to_thread(
        score_repo.get_recent_tasks_sync, feature, limit)
    return ApiResponse(data={"tasks": tasks})
