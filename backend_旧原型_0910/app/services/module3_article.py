"""模块三：AI 三级文章生成（大纲 → 初稿 → 定稿，黑盒工具页）。

固定 AI 角色（用户不可见）：
- 大纲生成：官方渠道 AI（deepseek）
- 初稿撰写：中转渠道 AI（claude）
- 定稿审核：官方渠道 AI（zhipu）
修改迭代直接复用定稿 AI，无需重跑全链路。
"""
from __future__ import annotations

import uuid

from app.ai.base import StreamResult, sys_msg, user_msg
from app.ai.mock_engine import mock_draft, mock_final, mock_outline, mock_revision
from app.ai.scheduler import scheduler
from app.core import database as db
from app.prompts import library as P

ROLE_OUTLINE = "deepseek"
ROLE_DRAFT = "claude"
ROLE_FINAL = "zhipu"

STAGES = [
    ("outline", "第一棒 · 大纲生成"),
    ("draft", "第二棒 · 初稿撰写"),
    ("finalize", "第三棒 · 定稿审核"),
]


async def run(task, topic: str, genre: str, style: str, length: str, requirement: str) -> dict:
    article_id = uuid.uuid4().hex[:12]
    need = (
        f"主题：{topic}\n文体：{genre}\n风格：{style}\n篇幅：{length}\n"
        f"特殊要求：{requirement or '无'}"
    )
    await db.execute(
        "INSERT INTO articles (id, topic, genre, style, length, requirement, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (article_id, topic, genre, style, length, requirement, "running", db.now(), db.now()),
    )
    await task.emit("article", articleId=article_id)

    outline = draft = final_text = ""

    for key, label in STAGES:
        await task.emit("stage", key=key, label=label, status="running")

        if key == "outline":
            prompt = P.M3_OUTLINE.format(genre=genre or "通用", need=need)
            role, mock_text = ROLE_OUTLINE, mock_outline(topic, genre, style, length)
        elif key == "draft":
            prompt = P.M3_DRAFT.format(genre=genre or "通用", length=length or "中篇", outline=outline)
            role, mock_text = ROLE_DRAFT, mock_draft(topic, outline)
        else:
            prompt = P.M3_FINALIZE.format(need=need, draft=draft)
            role, mock_text = ROLE_FINAL, mock_final(topic, draft)

        result = StreamResult()
        text = ""
        async for piece in scheduler.stream(
            role,
            [sys_msg(P.SYSTEM_WRITER), user_msg(prompt)],
            result, temperature=0.7, max_tokens=3072,
            mock_text=mock_text,
        ):
            text += piece
            await task.emit("delta", channel=key, text=piece)

        text = text.strip()
        if result.fallback:
            await task.emit("notice", level="warning",
                            text=f"{label} 调用异常，已降级为本地引擎（{result.error}）")

        if key == "outline":
            outline = text
            await task.emit("stageResult", stage="outline", content=outline)
        elif key == "draft":
            draft = text
            await task.emit("stageResult", stage="draft", content=draft)
        else:
            final_text = text
            await task.emit("stageResult", stage="finalize", content=final_text)
        await task.emit("stage", key=key, label=label, status="done")

    await db.execute(
        "UPDATE articles SET outline=?, draft=?, final_text=?, status=?, updated_at=? WHERE id=?",
        (outline, draft, final_text, "finished", db.now(), article_id),
    )
    await task.finish("done", articleId=article_id,
                      outline=outline, draft=draft, final=final_text)
    return {"article_id": article_id, "final": final_text}


async def revise(task, article_id: str, instruction: str) -> str:
    """基于用户修改意见迭代定稿。"""
    row = await db.query_one("SELECT * FROM articles WHERE id=?", (article_id,))
    base_text = (row or {}).get("final_text") or ""

    await task.emit("stage", key="revise", label="修改迭代", status="running")
    result = StreamResult()
    text = ""
    async for piece in scheduler.stream(
        ROLE_FINAL,
        [sys_msg(P.SYSTEM_WRITER),
         user_msg(P.M3_REVISE.format(text=base_text, instruction=instruction))],
        result, temperature=0.7, max_tokens=3072,
        mock_text=mock_revision(base_text, instruction),
    ):
        text += piece
        await task.emit("delta", channel="revise", text=piece)

    text = text.strip()
    await db.execute("UPDATE articles SET final_text=?, updated_at=? WHERE id=?",
                     (text, db.now(), article_id))
    await task.emit("stage", key="revise", label="修改迭代", status="done")
    await task.finish("done", articleId=article_id, final=text)
    return text
