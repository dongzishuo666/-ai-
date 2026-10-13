# ============================================================
# 文件：backend/app/services/article_pipeline.py
# 说明：功能三流水线。outliner → writer → reviewer 严格串行。
# 约束：
#   - 全部 DB 操作经 asyncio.to_thread 包装
#   - 最外层 try/except/finally 兜底，确保落到 done/failed（提醒1）
#   - 事件通过 sse_manager 推送（内存广播）
# ============================================================

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import ARTICLE_GEN_ROLES, AI_MODELS
from app.config.prompts import (
    ARTICLE_OUTLINER_SYSTEM,
    ARTICLE_OUTLINER_USER,
    ARTICLE_REVIEWER_SYSTEM,
    ARTICLE_REVIEWER_USER,
    ARTICLE_WRITER_SYSTEM,
    ARTICLE_WRITER_USER,
    GENRE_LABELS,
    TEAM_AWARENESS,
    TEAM_ROLE_OUTLINER,
    TEAM_ROLE_REVIEWER,
    TEAM_ROLE_WRITER,
    build_prompt,
)
from app.database import article_repo
from app.services.ai_scheduler import call_ai
from app.services.quality_inspector import inspect_quality
from app.services.sse_manager import sse_manager

logger = logging.getLogger("app.article_pipeline")


# ============================================================
# 工具
# ============================================================

def _count_words(text: str) -> int:
    """字数统计：去除所有空白后的字符数（适配中文）。"""
    return len(re.sub(r"\s", "", text or ""))


def _split_revision(content: str) -> Tuple[str, Optional[str]]:
    """
    解析 reviewer 输出：定稿 + 修改说明。
    分隔符：最后一个 "\\n---\\n"。
    无分隔符时整体视为定稿，revision_notes=None。
    """
    marker = "\n---\n"
    if marker in content:
        parts = content.rsplit(marker, 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[0].rstrip(), parts[1].strip()
    return content, None


def _opt_str(value: Optional[str], default: str = "无特殊要求") -> str:
    """将 None 转为占位文本，避免 Prompt 出现 'None'。"""
    if value is None or str(value).strip() == "":
        return default
    return str(value)


def _team_block(role_line: str, peers_line: str) -> str:
    """
    团队意识注入块：让每个环节的模型知道自己只是文章流水线（策划→撰稿→审核）
    上的一环，上游材料要承接、下游要能接手，不越界、不把局部输出当成定论。
    """
    return "\n\n" + build_prompt(
        TEAM_AWARENESS, role_line=role_line, peers_line=peers_line)


# ============================================================
# 消息构建
# ============================================================

def _build_outline_messages(article: Dict[str, Any]) -> List[Dict[str, str]]:
    user = build_prompt(
        ARTICLE_OUTLINER_USER,
        topic=article["topic"],
        genre_label=GENRE_LABELS.get(article["genre"], "通用文章"),
        target_word_count=article["target_word_count"],
        style=_opt_str(article.get("style")),
        audience=_opt_str(article.get("audience")),
        additional_requirements=_opt_str(article.get("additional_requirements")),
    )
    return [
        {"role": "system", "content": ARTICLE_OUTLINER_SYSTEM + _team_block(
            TEAM_ROLE_OUTLINER,
            "协作席位：「撰稿」「审核定稿」，它们会接着你的大纲往下做，"
            "所以大纲要写到别人能直接开工的程度。")},
        {"role": "user", "content": user},
    ]


def _build_draft_messages(article: Dict[str, Any], outline: str) -> List[Dict[str, str]]:
    user = build_prompt(
        ARTICLE_WRITER_USER,
        outline_content=outline,
        genre_label=GENRE_LABELS.get(article["genre"], "通用文章"),
        target_word_count=article["target_word_count"],
        style=_opt_str(article.get("style")),
        audience=_opt_str(article.get("audience")),
        additional_requirements=_opt_str(article.get("additional_requirements")),
    )
    # Writer 的 System 含 {target_word_count} 变量，需填充
    system = build_prompt(
        ARTICLE_WRITER_SYSTEM,
        target_word_count=article["target_word_count"],
    ) + _team_block(
        TEAM_ROLE_WRITER,
        "协作席位：「大纲策划」给了你大纲，「审核定稿」会在你之后把关，"
        "所以请严格按大纲写，缺什么就写明，不要自行改结构。")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_review_messages(
    article: Dict[str, Any], outline: str, draft: str
) -> List[Dict[str, str]]:
    user = build_prompt(
        ARTICLE_REVIEWER_USER,
        outline_content=outline,
        draft_content=draft,
        genre_label=GENRE_LABELS.get(article["genre"], "通用文章"),
        target_word_count=article["target_word_count"],
        style=_opt_str(article.get("style")),
        audience=_opt_str(article.get("audience")),
    )
    return [
        {"role": "system", "content": ARTICLE_REVIEWER_SYSTEM + _team_block(
            TEAM_ROLE_REVIEWER,
            "协作席位：上游是「大纲策划」与「撰稿」两个席位；你只做审核打磨，"
            "发现结构性问题时指出它并说明理由，不要推倒重来。")},
        {"role": "user", "content": user},
    ]


# ============================================================
# 单阶段执行
# ============================================================

async def _run_one_stage(
    *,
    article: Dict[str, Any],
    stage: str,
    role: str,
    messages: List[Dict[str, str]],
    stream_id: str,
    ai_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行单个阶段。返回 {output, word_count, duration_ms, revision_notes}。
    失败时抛 RuntimeError。
    """
    article_id = article["article_id"]
    cfg = ARTICLE_GEN_ROLES[role]
    # 用户可自选每级写作使用哪个模型（降成本）；未指定或非法时回落默认配置
    ai_id = ai_override or cfg["ai_id"]
    if ai_id not in AI_MODELS:
        logger.warning("自选模型 %s 未注册，回落默认 %s", ai_id, cfg["ai_id"])
        ai_id = cfg["ai_id"]
    fallback = cfg.get("fallback", [])

    # 定位 stage_id
    stages = await asyncio.to_thread(article_repo.get_stages_sync, article_id)
    stage_row = next((s for s in stages if s["stage"] == stage), None)
    if stage_row is None:
        raise RuntimeError(f"阶段记录缺失: article={article_id} stage={stage}")
    stage_id = stage_row["id"]

    # 落 running + 推 stage_start
    input_content = messages[-1]["content"][:2000]
    await asyncio.to_thread(article_repo.mark_stage_running_sync, stage_id, input_content)
    await sse_manager.publish(stream_id, "stage_start", {
        "stage": stage,
        "role": role,
        "model": ai_id,
        "description": cfg["description"],
    })

    # 调用 AI
    result = await call_ai(
        ai_id,
        messages,
        task_type="article_gen",
        task_id=article_id,
        fallback=fallback,
    )

    if result.status != "success":
        err = result.error_message or "AI 调用失败"
        await asyncio.to_thread(article_repo.mark_stage_failed_sync, stage_id, err)
        # 推送 stage_failed 事件（非致命，由外层 error 事件终结）
        await sse_manager.publish(stream_id, "stage_failed", {
            "stage": stage,
            "role": role,
            "reason": result.status,
            "fallback": "none",
            "message": err,
        })
        raise RuntimeError(f"阶段 {stage} 失败: {err}")

    # 解析输出
    output = result.content
    revision_notes: Optional[str] = None
    if stage == "revision":
        output, revision_notes = _split_revision(output)

    word_count = _count_words(output)
    duration_ms = result.duration_ms

    await asyncio.to_thread(
        article_repo.mark_stage_done_sync,
        stage_id,
        output_content=output,
        word_count=word_count,
        duration_ms=duration_ms,
        revision_notes=revision_notes,
        # 降级后实际产出模型与计划模型不同，回写真实值
        # （表里存的是 model_name，与建阶段时保持一致）
        actual_model=(
            AI_MODELS[result.ai_id].model_name
            if result.ai_id in AI_MODELS else result.ai_id
        ),
    )

    # 推送 stage_done
    stage_done_payload: Dict[str, Any] = {
        "stage": stage,
        "status": "done",
        "model": result.ai_id,
        "duration_ms": duration_ms,
        "word_count": word_count,
    }
    if stage == "outline":
        stage_done_payload["outline_preview"] = output[:200]
    await sse_manager.publish(stream_id, "stage_done", stage_done_payload)

    return {
        "output": output,
        "word_count": word_count,
        "duration_ms": duration_ms,
        "revision_notes": revision_notes,
        # 实际出场的模型（降级后会与计划模型不同），质检打分要用真实值
        "ai_id": result.ai_id,
        "display_name": (
            AI_MODELS[result.ai_id].display_name
            if result.ai_id in AI_MODELS else result.ai_id
        ),
    }


# ============================================================
# 主流水线
# ============================================================

async def run_article_pipeline(
    article_id: int, roles: Optional[Dict[str, str]] = None
) -> None:
    """
    文章生成主流水线。
    由 GET /api/articles/{id}/stream 首次连接时启动。
    """
    stream_id = f"article_{article_id}"
    pipeline_start = time.monotonic()

    try:
        # 0. 读取文章参数
        article = await asyncio.to_thread(article_repo.get_article_sync, article_id)
        if article is None:
            logger.error("文章不存在: id=%s", article_id)
            return

        # 1. 种子三阶段（幂等）
        # 记录实际将使用的模型（用户自选优先），否则阶段表会显示默认配置里的模型
        def _model_of(role: str) -> str:
            ai_id = (roles or {}).get(role) or ARTICLE_GEN_ROLES[role]["ai_id"]
            if ai_id not in AI_MODELS:
                ai_id = ARTICLE_GEN_ROLES[role]["ai_id"]
            return AI_MODELS[ai_id].model_name

        outliner_model = _model_of("outliner")
        writer_model = _model_of("writer")
        reviewer_model = _model_of("reviewer")
        await asyncio.to_thread(
            article_repo.seed_stages_sync,
            article_id,
            outliner=outliner_model,
            writer=writer_model,
            reviewer=reviewer_model,
        )

        # 2. 推 stream_start
        await sse_manager.publish(stream_id, "stream_start", {
            "stream_id": stream_id,
            "type": "article_gen",
            "article_id": article_id,
            "created_at": article["created_at"],
        })

        # 3. Stage 1: outline
        outline_result = await _run_one_stage(
            article=article,
            stage="outline",
            role="outliner",
            messages=_build_outline_messages(article),
            stream_id=stream_id,
            ai_override=(roles or {}).get("outliner"),
        )
        await asyncio.to_thread(
            article_repo.update_article_status_sync, article_id, "outline_ready"
        )
        await sse_manager.publish(stream_id, "outline_ready", {
            "outline": outline_result["output"],
        })

        # 4. Stage 2: draft
        await asyncio.to_thread(
            article_repo.update_article_status_sync, article_id, "writing"
        )
        draft_result = await _run_one_stage(
            article=article,
            stage="draft",
            role="writer",
            messages=_build_draft_messages(article, outline_result["output"]),
            stream_id=stream_id,
            ai_override=(roles or {}).get("writer"),
        )

        # 5. Stage 3: revision
        await asyncio.to_thread(
            article_repo.update_article_status_sync, article_id, "revising"
        )
        revision_result = await _run_one_stage(
            article=article,
            stage="revision",
            role="reviewer",
            messages=_build_review_messages(
                article, outline_result["output"], draft_result["output"]
            ),
            stream_id=stream_id,
            ai_override=(roles or {}).get("reviewer"),
        )

        # 6. 完成
        total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
        await asyncio.to_thread(
            article_repo.update_article_final_word_count_sync,
            article_id,
            revision_result["word_count"],
            total_duration_ms,
        )
        await asyncio.to_thread(
            article_repo.update_article_status_sync,
            article_id, "done", completed=True,
        )

        # 6.5 最终质量检测员：给三个环节 AI 打分并推质检榜
        #     质检只影响榜单，不影响成品；失败时静默，不打断交付。
        try:
            await inspect_quality(
                feature="article",
                task_id=article_id,
                requirement=(
                    f"主题：{article['topic']}\n"
                    f"体裁：{GENRE_LABELS.get(article['genre'], '通用文章')}\n"
                    f"目标字数：{article['target_word_count']}\n"
                    f"风格要求：{article.get('style') or '无'}\n"
                    f"目标读者：{article.get('audience') or '无'}"
                ),
                stages=[
                    {"role": "outliner", "role_label": "大纲策划",
                     "ai_id": outline_result["ai_id"],
                     "display_name": outline_result["display_name"],
                     "output": outline_result["output"],
                     "duration_ms": outline_result["duration_ms"],
                     "word_count": outline_result["word_count"],
                     "status": "done"},
                    {"role": "writer", "role_label": "撰稿",
                     "ai_id": draft_result["ai_id"],
                     "display_name": draft_result["display_name"],
                     "output": draft_result["output"],
                     "duration_ms": draft_result["duration_ms"],
                     "word_count": draft_result["word_count"],
                     "status": "done"},
                    {"role": "reviewer", "role_label": "审核定稿",
                     "ai_id": revision_result["ai_id"],
                     "display_name": revision_result["display_name"],
                     "output": revision_result["output"],
                     "duration_ms": revision_result["duration_ms"],
                     "word_count": revision_result["word_count"],
                     "status": "done"},
                ],
                stream_id=stream_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("质量质检失败（不影响成品）: article_id=%s", article_id)

        # 7. 推 final_result + done
        await sse_manager.publish(stream_id, "final_result", {
            "article": revision_result["output"],
            "word_count": revision_result["word_count"],
            "total_duration_ms": total_duration_ms,
            "revision_notes": revision_result["revision_notes"] or "",
        })
        await sse_manager.publish(stream_id, "done", {})

    except Exception as e:  # noqa: BLE001
        logger.exception("文章流水线失败: article_id=%s", article_id)
        # 兜底：确保状态落到 failed（提醒1）
        try:
            await asyncio.to_thread(
                article_repo.update_article_status_sync,
                article_id, "failed",
                error_message=str(e),
                completed=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("兜底更新文章状态失败: article_id=%s", article_id)

        try:
            await sse_manager.publish(stream_id, "error", {
                "message": "文章生成失败，请稍后重试",
                "code": "INTERNAL_ERROR",
            })
            await sse_manager.publish(stream_id, "done", {})
        except Exception:  # noqa: BLE001
            logger.exception("推送错误事件失败")
