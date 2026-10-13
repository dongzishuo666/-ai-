"""模块二：多 AI 自动讨论 路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.registry import ALL_IDS
from app.core import database as db
from app.core.tasks import hub
from app.routers.common import launch, sse_response
from app.services import module2_discussion as svc

router = APIRouter(prefix="/api/module2", tags=["module2"])


class DiscussionRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    mode: str = "planning"
    participants: list[str] = Field(default_factory=list)
    judge: str = ""
    maxRounds: int = 4
    untilConsensus: bool = True
    customRules: str = ""
    framework: str = ""


@router.post("/discussions")
async def create_discussion(payload: DiscussionRequest) -> dict:
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="话题不能为空")
    participants = [p for p in payload.participants if p in ALL_IDS]
    max_rounds = max(1, min(payload.maxRounds, 12))

    channel = hub.create()
    launch(
        lambda ch: svc.run(
            ch, topic, payload.mode, participants, payload.judge, max_rounds,
            payload.untilConsensus, payload.customRules, payload.framework,
        ),
        channel,
    )
    return {"taskId": channel.task_id}


@router.get("/stream/{task_id}")
async def stream(task_id: str):
    channel = hub.get(task_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return sse_response(channel)


@router.post("/discussions/{discussion_id}/stop")
async def stop(discussion_id: str) -> dict:
    svc.request_stop(discussion_id)
    return {"stopped": True, "discussionId": discussion_id}


@router.get("/discussions")
async def list_discussions(limit: int = 20) -> dict:
    rows = await db.query(
        "SELECT id, topic, mode, status, judge, created_at FROM discussions "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {"items": rows}


@router.get("/discussions/{discussion_id}")
async def discussion_detail(discussion_id: str) -> dict:
    row = await db.query_one("SELECT * FROM discussions WHERE id=?", (discussion_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="讨论不存在")
    messages = await db.query(
        "SELECT * FROM messages WHERE discussion_id=? ORDER BY id ASC", (discussion_id,)
    )
    board = await db.query(
        "SELECT * FROM scoreboard WHERE discussion_id=? ORDER BY score DESC", (discussion_id,)
    )
    row["participants"] = db.load(row.get("participants"), [])
    return {"discussion": row, "messages": messages, "scoreboard": board}
