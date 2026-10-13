"""模块一：文档智能 Prompt 生成（三棒串行 · 黑盒工具页）。

固定 AI 角色（用户不可见）：
- 需求解析 + 方案生成：官方渠道 AI（deepseek）
- 方案优化：中转渠道 AI（claude）
- 汇总审定：官方渠道 AI（zhipu）
"""
from __future__ import annotations

from app.ai.base import StreamResult, sys_msg, user_msg
from app.ai.mock_engine import mock_prompt_stage
from app.ai.scheduler import scheduler
from app.core import database as db
from app.prompts import library as P

ROLE_PARSE = "deepseek"
ROLE_OPTIMIZE = "claude"
ROLE_FINALIZE = "zhipu"

STAGES = [
    ("parse", "解析文档与需求"),
    ("generate", "第一棒 · 方案生成"),
    ("optimize", "第二棒 · 方案优化"),
    ("finalize", "第三棒 · 汇总定稿"),
]


async def run(task, need: str, doc_text: str, doc_name: str, doc_type: str) -> dict:
    """执行三棒串行链路，全程通过 SSE 推送阶段状态。"""
    engines: list[str] = []
    prev = ""
    parsed = ""

    for key, label in STAGES:
        await task.emit("stage", key=key, label=label, status="running")

        if key == "parse":
            prompt = P.M1_PARSE.format(need=need, doc=doc_text or "（无文档）")
        elif key == "generate":
            prompt = P.M1_GENERATE.format(doc=doc_text or "（无文档）", parsed=parsed)
        elif key == "optimize":
            prompt = P.M1_OPTIMIZE.format(prev=prev)
        else:
            prompt = P.M1_FINALIZE.format(need=need, doc=doc_text or "（无文档）", prev=prev)

        role = {"parse": ROLE_PARSE, "generate": ROLE_PARSE,
                "optimize": ROLE_OPTIMIZE, "finalize": ROLE_FINALIZE}[key]

        result = StreamResult()
        chunks: list[str] = []
        # 仅最终定稿阶段向前端流式输出（黑盒：过程不暴露给用户）
        stream_out = key == "finalize"
        async for piece in scheduler.stream(
            role,
            [sys_msg(P.SYSTEM_GENERAL), user_msg(prompt)],
            result,
            temperature=0.6,
            max_tokens=2048,
            mock_text=mock_prompt_stage(key, need, doc_text, prev),
        ):
            chunks.append(piece)
            if stream_out:
                await task.emit("delta", channel="result", text=piece)

        text = "".join(chunks).strip()
        engines.append(f"{key}:{result.engine}")
        if result.fallback:
            await task.emit("notice", level="warning",
                            text=f"{label} 调用异常，已降级为本地引擎（{result.error}）")

        prev = text
        if key == "parse":
            parsed = text
        await task.emit("stage", key=key, label=label, status="done")

    final_prompt = prev
    history_id = await db.execute(
        "INSERT INTO prompt_history (need, doc_name, doc_type, result, engine, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (need, doc_name, doc_type, final_prompt, ",".join(engines), db.now()),
    )
    await task.emit("result", content=final_prompt)
    await task.finish("done", historyId=history_id, engines=engines)
    return {"result": final_prompt, "history_id": history_id}
