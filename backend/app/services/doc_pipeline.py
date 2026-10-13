# ============================================================
# 文件：backend/app/services/doc_pipeline.py
# 说明：功能一流水线。planner → optimizer → finalizer 严格串行。
#   产物语义：三棒产出的是「按用户需求完成的成品」本身，不是用于生成成品的 Prompt。
# 约束：
#   - 全部 DB 操作经 asyncio.to_thread 包装
#   - 最外层 try/except/finally 兜底落 done/failed（僵尸任务防护）
#   - 步骤跳过规则（蓝图4.1）：
#       · step1 失败 → 整体 failed（无基础方案）
#       · step2 失败 → 跳过，将 step1 输出传给 step3
#       · step3 失败 → 跳过，将 step2（或 step1）输出作为最终结果
# ============================================================

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.config import AI_MODELS, DOC_PROCESS_ROLES
from app.config.prompts import (
    DOC_FINALIZER_SYSTEM,
    DOC_FINALIZER_USER,
    DOC_OPTIMIZER_SYSTEM,
    DOC_OPTIMIZER_USER,
    DOC_PLANNER_SYSTEM,
    DOC_PLANNER_USER,
    TEAM_AWARENESS,
    TEAM_ROLE_DOC_FINALIZER,
    TEAM_ROLE_DOC_OPTIMIZER,
    TEAM_ROLE_DOC_PLANNER,
    build_prompt,
)
from app.database import doc_repo
from app.services.ai_scheduler import call_ai
from app.services.quality_inspector import inspect_quality
from app.services.sse_manager import sse_manager

logger = logging.getLogger("app.doc_pipeline")


# 摘要预览长度（用于 parse_done 事件与 {doc_summary} 注入）
_SUMMARY_PREVIEW_LEN = 200
# 步骤 output_preview 长度（蓝图 2.1.2 字段说明）
_STEP_PREVIEW_LEN = 200
# 审校 / 终审棒注入的文档原文上限：成品必须以原文为依据，
# 但全文重复注入两遍会显著抬高 token 成本，故截断到该长度。
_DOC_CONTEXT_MAX = 12000


# ============================================================
# 工具
# ============================================================

def _make_summary(raw_text: str) -> str:
    """
    生成摘要预览（不调用 AI）：
    取原文前 200 字符，压缩换行与多余空格。
    """
    if not raw_text:
        return ""
    head = raw_text[:_SUMMARY_PREVIEW_LEN]
    # 压缩换行
    head = head.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # 压缩多空格
    while "  " in head:
        head = head.replace("  ", " ")
    return head.strip()


def _opt_str(value: Optional[str], default: str = "无特殊要求") -> str:
    if value is None or str(value).strip() == "":
        return default
    return str(value)


def _team_block(role_line: str, peers_line: str) -> str:
    """
    团队意识注入块：让每个环节的模型知道自己是文档流水线
    （成稿策划 → 审校打磨 → 终审整合）上的一环：承接上游、交付下游、不越界。
    """
    return "\n\n" + build_prompt(
        TEAM_AWARENESS, role_line=role_line, peers_line=peers_line)


def _doc_context(task: Dict[str, Any]) -> str:
    """
    审校 / 终审棒可见的文档原文。

    成品必须基于原文事实，只给摘要会让模型靠想象补全，因此这里注入原文；
    超长文档截断，避免三棒 token 成本翻倍。
    """
    raw = (task.get("raw_text") or "").strip()
    if not raw:
        return "（文档原文为空）"
    if len(raw) > _DOC_CONTEXT_MAX:
        return (
            raw[:_DOC_CONTEXT_MAX]
            + f"\n\n（原文过长，以上为前 {_DOC_CONTEXT_MAX} 字符）"
        )
    return raw


# ============================================================
# 消息构建
# ============================================================

def _build_planner_messages(task: Dict[str, Any]) -> List[Dict[str, str]]:
    user = build_prompt(
        DOC_PLANNER_USER,
        doc_type=task["doc_type"],
        doc_content=task["raw_text"] or "",
        doc_summary=_opt_str(task.get("summary"), "（无摘要）"),
        user_need=_opt_str(task.get("user_need")),
    )
    return [
        {"role": "system", "content": DOC_PLANNER_SYSTEM + _team_block(
            TEAM_ROLE_DOC_PLANNER,
            "协作席位：「审校打磨」「终审整合」，它们会在你之后接手，"
            "所以初稿要结构完整、事实可追溯，不要留半成品。")},
        {"role": "user", "content": user},
    ]


def _build_optimizer_messages(
    task: Dict[str, Any], draft_result: str
) -> List[Dict[str, str]]:
    user = build_prompt(
        DOC_OPTIMIZER_USER,
        doc_type=task["doc_type"],
        doc_summary=_opt_str(task.get("summary"), "（无摘要）"),
        doc_content=_doc_context(task),
        user_need=_opt_str(task.get("user_need")),
        draft_result=draft_result,
    )
    return [
        {"role": "system", "content": DOC_OPTIMIZER_SYSTEM + _team_block(
            TEAM_ROLE_DOC_OPTIMIZER,
            "协作席位：上游是「成稿策划」席位，下游是「终审主编」席位；"
            "在其初稿上打磨，不要另起一份。")},
        {"role": "user", "content": user},
    ]


def _build_finalizer_messages(
    task: Dict[str, Any], draft_result: str, optimized_result: str
) -> List[Dict[str, str]]:
    user = build_prompt(
        DOC_FINALIZER_USER,
        user_need=_opt_str(task.get("user_need")),
        doc_summary=_opt_str(task.get("summary"), "（无摘要）"),
        doc_content=_doc_context(task),
        draft_result=draft_result,
        optimized_result=optimized_result,
    )
    return [
        {"role": "system", "content": DOC_FINALIZER_SYSTEM + _team_block(
            TEAM_ROLE_DOC_FINALIZER,
            "协作席位：上游「成稿策划」与「审校打磨」两个席位的成果都要纳入，"
            "整合时保留各自的有效部分，不要只取一份而丢弃另一份的贡献。")},
        {"role": "user", "content": user},
    ]


# ============================================================
# 单步骤执行
# ============================================================

async def _run_one_step(
    *,
    task: Dict[str, Any],
    step_number: int,
    role: str,
    messages: List[Dict[str, str]],
    stream_id: str,
    ai_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行单个步骤。返回：
        {success, output, duration_ms, prompt_tokens, completion_tokens,
         total_tokens, error_message}
    - 失败时不抛异常，返回 success=False
    - 由调用方决定是否跳过
    """
    task_id = task["task_id"]
    cfg = DOC_PROCESS_ROLES[role]
    # 用户可自选每棒使用哪个模型（降成本）；未指定或指定非法时回落默认配置
    ai_id = ai_override or cfg["ai_id"]
    if ai_id not in AI_MODELS:
        logger.warning("自选模型 %s 未注册，回落默认 %s", ai_id, cfg["ai_id"])
        ai_id = cfg["ai_id"]
    fallback = cfg.get("fallback", [])

    # 定位 step_id
    steps = await asyncio.to_thread(doc_repo.get_doc_steps_sync, task_id)
    step_row = next((s for s in steps if s["step_number"] == step_number), None)
    if step_row is None:
        raise RuntimeError(f"步骤记录缺失: task={task_id} step={step_number}")
    step_id = step_row["id"]

    # 落 running + 推 step_start
    input_prompt = messages[-1]["content"][:2000]
    await asyncio.to_thread(doc_repo.mark_doc_step_running_sync, step_id, input_prompt)
    await sse_manager.publish(stream_id, "step_start", {
        "step": step_number,
        "role": role,
        "model": ai_id,
        "description": cfg["description"],
    })

    # AI 调用
    result = await call_ai(
        ai_id,
        messages,
        task_type="doc_process",
        task_id=task_id,
        fallback=fallback,
    )

    if result.status != "success":
        err = result.error_message or "AI 调用失败"
        await asyncio.to_thread(doc_repo.mark_doc_step_failed_sync, step_id, err)
        return {
            "success": False,
            "output": "",
            "duration_ms": result.duration_ms,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "error_message": err,
            "step_id": step_id,
            "ai_id": ai_id,
            "display_name": (
                AI_MODELS[ai_id].display_name if ai_id in AI_MODELS else ai_id),
        }

    output = result.content or ""
    preview = output[:_STEP_PREVIEW_LEN]

    await asyncio.to_thread(
        doc_repo.mark_doc_step_done_sync,
        step_id,
        output_content=output,
        output_preview=preview,
        duration_ms=result.duration_ms,
        # 降级后实际产出模型与计划模型不同，回写真实值
        # （表里存的是 model_name，与 seed 时保持一致）
        actual_model=(
            AI_MODELS[result.ai_id].model_name
            if result.ai_id in AI_MODELS else result.ai_id
        ),
    )
    await sse_manager.publish(stream_id, "step_done", {
        "step": step_number,
        "role": role,
        "status": "done",
        "model": result.ai_id,
        "duration_ms": result.duration_ms,
        "preview": preview,
    })

    return {
        "success": True,
        "output": output,
        "duration_ms": result.duration_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "error_message": None,
        "step_id": step_id,
        # 实际出场的模型（降级后与计划模型不同），质检打分要用真实值
        "ai_id": result.ai_id,
        "display_name": (
            AI_MODELS[result.ai_id].display_name
            if result.ai_id in AI_MODELS else result.ai_id
        ),
    }


async def _mark_step_skipped(step_id: int, reason: str) -> None:
    await asyncio.to_thread(doc_repo.mark_doc_step_skipped_sync, step_id, reason)


# ============================================================
# 主流水线
# ============================================================

async def run_doc_pipeline(
    task_id: int, roles: Optional[Dict[str, str]] = None
) -> None:
    """
    文档成品生成主流水线（产物是成品本身，不是 Prompt）。
    由 GET /api/docs/{id}/stream 首次连接时启动。
    """
    stream_id = f"doc_{task_id}"
    pipeline_start = time.monotonic()

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    try:
        # 0. 读取任务
        task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
        if task is None:
            logger.error("文档任务不存在: id=%s", task_id)
            return

        # 1. 生成摘要预览（不调用AI）并落库
        summary = _make_summary(task.get("raw_text") or "")
        await asyncio.to_thread(doc_repo.update_doc_task_summary_sync, task_id, summary)
        task["summary"] = summary

        # 2. 种子三步（幂等）
        planner_model = AI_MODELS[DOC_PROCESS_ROLES["planner"]["ai_id"]].model_name
        optimizer_model = AI_MODELS[DOC_PROCESS_ROLES["optimizer"]["ai_id"]].model_name
        finalizer_model = AI_MODELS[DOC_PROCESS_ROLES["finalizer"]["ai_id"]].model_name
        await asyncio.to_thread(
            doc_repo.seed_doc_steps_sync,
            task_id,
            planner=planner_model,
            optimizer=optimizer_model,
            finalizer=finalizer_model,
        )

        # 3. stream_start
        await sse_manager.publish(stream_id, "stream_start", {
            "stream_id": stream_id,
            "type": "doc_process",
            "task_id": task_id,
            "created_at": task["created_at"],
        })

        # 4. parse_done（文档解析信息）
        await sse_manager.publish(stream_id, "parse_done", {
            "doc_type": task["doc_type"],
            "char_count": task["char_count"],
            "summary_preview": summary,
        })

        # 5. Step 1: planner（失败则整体失败）
        step1 = await _run_one_step(
            task=task, step_number=1, role="planner",
            messages=_build_planner_messages(task),
            stream_id=stream_id,
            ai_override=(roles or {}).get("planner"),
        )
        total_prompt_tokens += step1["prompt_tokens"]
        total_completion_tokens += step1["completion_tokens"]
        total_tokens += step1["total_tokens"]

        if not step1["success"]:
            raise RuntimeError(f"planner 步骤失败: {step1['error_message']}")

        draft_result = step1["output"]

        # 6. Step 2: optimizer（失败则跳过）
        step2 = await _run_one_step(
            task=task, step_number=2, role="optimizer",
            messages=_build_optimizer_messages(task, draft_result),
            stream_id=stream_id,
            ai_override=(roles or {}).get("optimizer"),
        )
        total_prompt_tokens += step2["prompt_tokens"]
        total_completion_tokens += step2["completion_tokens"]
        total_tokens += step2["total_tokens"]

        if step2["success"]:
            optimized_result = step2["output"]
        else:
            await _mark_step_skipped(
                step2["step_id"],
                f"optimizer 步骤失败，已跳过：{step2['error_message']}",
            )
            await sse_manager.publish(stream_id, "step_failed", {
                "step": 2,
                "role": "optimizer",
                "reason": "timeout_or_error",
                "fallback": "skipped",
                "message": "审校步骤失败，已跳过，使用初稿继续",
            })
            optimized_result = draft_result

        # 7. Step 3: finalizer（失败则跳过）
        step3 = await _run_one_step(
            task=task, step_number=3, role="finalizer",
            messages=_build_finalizer_messages(
                task, draft_result, optimized_result),
            stream_id=stream_id,
            ai_override=(roles or {}).get("finalizer"),
        )
        total_prompt_tokens += step3["prompt_tokens"]
        total_completion_tokens += step3["completion_tokens"]
        total_tokens += step3["total_tokens"]

        if step3["success"]:
            final_output = step3["output"]
        else:
            await _mark_step_skipped(
                step3["step_id"],
                f"finalizer 步骤失败，已跳过：{step3['error_message']}",
            )
            await sse_manager.publish(stream_id, "step_failed", {
                "step": 3,
                "role": "finalizer",
                "reason": "timeout_or_error",
                "fallback": "skipped",
                "message": "终审步骤失败，已跳过，使用审校稿作为最终成品",
            })
            final_output = optimized_result

        # 8. 完成
        total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
        await asyncio.to_thread(
            doc_repo.update_doc_task_final_prompt_sync,
            task_id, final_output, total_duration_ms,
        )
        await asyncio.to_thread(
            doc_repo.update_doc_task_status_sync,
            task_id, "done", completed=True,
        )

        # 8.5 最终质量检测员：给三个环节 AI 打分并推质检榜
        #     只影响榜单，不影响成品；失败时静默。
        try:
            await inspect_quality(
                feature="doc",
                task_id=task_id,
                requirement=(
                    f"文档类型：{task['doc_type']}\n"
                    f"用户需求：{task.get('user_need') or '无'}\n"
                    f"内容摘要：{task.get('summary') or '无'}"
                ),
                stages=[
                    {"role": "planner", "role_label": "成稿策划",
                     "ai_id": step1.get("ai_id"),
                     "display_name": step1.get("display_name"),
                     "output": draft_result,
                     "duration_ms": step1.get("duration_ms"),
                     "status": "done" if step1["success"] else "failed"},
                    {"role": "optimizer", "role_label": "审校打磨",
                     "ai_id": step2.get("ai_id"),
                     "display_name": step2.get("display_name"),
                     "output": optimized_result if step2["success"] else "",
                     "duration_ms": step2.get("duration_ms"),
                     "status": "done" if step2["success"] else "failed"},
                    {"role": "finalizer", "role_label": "终审整合",
                     "ai_id": step3.get("ai_id"),
                     "display_name": step3.get("display_name"),
                     "output": final_output if step3["success"] else "",
                     "duration_ms": step3.get("duration_ms"),
                     "status": "done" if step3["success"] else "failed"},
                ],
                stream_id=stream_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("质量质检失败（不影响成品）: task_id=%s", task_id)

        # 9. final_result + done
        await sse_manager.publish(stream_id, "final_result", {
            # prompt 字段保留以兼容旧前端；成品语义字段名是 result
            "prompt": final_output,
            "result": final_output,
            "total_duration_ms": total_duration_ms,
            "token_usage": {
                "input": total_prompt_tokens,
                "output": total_completion_tokens,
                "total": total_tokens,
            },
        })
        await sse_manager.publish(stream_id, "done", {})

    except Exception as e:  # noqa: BLE001
        logger.exception("文档流水线失败: task_id=%s", task_id)
        # 兜底：确保状态落 failed
        try:
            await asyncio.to_thread(
                doc_repo.update_doc_task_status_sync,
                task_id, "failed",
                error_message=str(e),
                completed=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("兜底更新文档任务状态失败: task_id=%s", task_id)

        try:
            await sse_manager.publish(stream_id, "error", {
                "message": "成品生成失败，请稍后重试",
                "code": "INTERNAL_ERROR",
            })
            await sse_manager.publish(stream_id, "done", {})
        except Exception:  # noqa: BLE001
            logger.exception("推送错误事件失败")
