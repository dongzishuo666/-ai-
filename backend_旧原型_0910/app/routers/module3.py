"""模块三：AI 三级文章生成 路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import database as db
from app.core.tasks import hub
from app.routers.common import launch, sse_response
from app.services import module3_article as svc

router = APIRouter(prefix="/api/module3", tags=["module3"])


class ArticleRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    genre: str = "通用"
    style: str = "中性"
    length: str = "中篇"
    requirement: str = ""


class ReviseRequest(BaseModel):
    articleId: str
    instruction: str = Field(..., min_length=1)


@router.post("/generate")
async def generate(payload: ArticleRequest) -> dict:
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="主题不能为空")
    channel = hub.create()
    launch(
        lambda ch: svc.run(ch, topic, payload.genre, payload.style,
                           payload.length, payload.requirement),
        channel,
    )
    return {"taskId": channel.task_id}


@router.post("/revise")
async def revise(payload: ReviseRequest) -> dict:
    row = await db.query_one("SELECT id FROM articles WHERE id=?", (payload.articleId,))
    if row is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    channel = hub.create()
    launch(lambda ch: svc.revise(ch, payload.articleId, payload.instruction), channel)
    return {"taskId": channel.task_id}


@router.get("/stream/{task_id}")
async def stream(task_id: str):
    channel = hub.get(task_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return sse_response(channel)


@router.get("/articles")
async def list_articles(limit: int = 20) -> dict:
    rows = await db.query(
        "SELECT id, topic, genre, style, length, status, created_at FROM articles "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {"items": rows}


@router.get("/articles/{article_id}")
async def article_detail(article_id: str) -> dict:
    row = await db.query_one("SELECT * FROM articles WHERE id=?", (article_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    return row
