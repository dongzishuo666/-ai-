"""模块一：文档智能 Prompt 生成 路由。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.core import database as db
from app.core.tasks import hub
from app.routers.common import launch, sse_response
from app.services import module1_prompt
from app.services.parser import extract_text

router = APIRouter(prefix="/api/module1", tags=["module1"])


@router.post("/generate")
async def generate(
    need: str = Form(""),
    file: UploadFile | None = File(default=None),
) -> dict:
    doc_text, doc_type, doc_name = "", "", ""
    if file is not None and file.filename:
        raw = await file.read()
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 限制")
        doc_text, doc_type = await extract_text(file.filename, raw)
        doc_name = file.filename
    if not need.strip() and not doc_text:
        raise HTTPException(status_code=400, detail="请至少提供需求描述或上传文档")

    channel = hub.create()
    launch(
        lambda ch: module1_prompt.run(ch, need, doc_text, doc_name, doc_type),
        channel,
    )
    return {"taskId": channel.task_id, "docType": doc_type, "docName": doc_name}


@router.get("/stream/{task_id}")
async def stream(task_id: str):
    channel = hub.get(task_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return sse_response(channel)


@router.get("/history")
async def history(limit: int = 20) -> dict:
    rows = await db.query(
        "SELECT id, need, doc_name, doc_type, created_at FROM prompt_history "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return {"items": rows}


@router.get("/history/{history_id}")
async def history_detail(history_id: int) -> dict:
    row = await db.query_one("SELECT * FROM prompt_history WHERE id=?", (history_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row
