# ============================================================
# 文件：backend/app/services/quality_inspector.py
# 说明：功能一（文档）/ 功能三（文章）的「最终质量检测员」。
#       流水线跑完后，由一个质检 AI 给参与的三个环节 AI 打分，
#       落库 ai_score_records 并推 SSE，前端据此渲染质检排行榜。
# 约束：
#   - DB 操作经 asyncio.to_thread 包装
#   - 质检失败不影响主流程（成品已经产出，只是没有榜单）
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import AI_MODELS
from app.config.business import DISCUSSION_JUDGES
from app.config.prompts import (
    QUALITY_INSPECTOR_SYSTEM,
    QUALITY_INSPECTOR_USER,
    build_prompt,
)
from app.database import score_repo
from app.services.ai_scheduler import call_ai
from app.services.sse_manager import sse_manager

logger = logging.getLogger("app.quality_inspector")

# 协作类型 → 展示文案 / 流水线描述（质检 prompt 用）
FEATURE_LABELS = {
    "article": ("文章生成", "大纲策划 → 撰稿 → 审核定稿"),
    "doc": ("文档成品生成", "成稿策划 → 审校打磨 → 终审整合"),
}

# 产出摘要长度：太长会把质检的上下文撑爆，太短看不出质量
_STAGE_SNIPPET = 1200


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里抠出第一个 JSON 对象（兼容 ```json 代码块）。"""
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _to_score(value: Any, default: float) -> float:
    try:
        s = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, s))


def _grade_of(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def _display_name(ai_id: str) -> str:
    cfg = AI_MODELS.get(ai_id)
    return cfg.display_name if cfg else ai_id


def _build_stages_text(stages: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for i, st in enumerate(stages, 1):
        head = (
            f"### 环节{i}：{st.get('role_label') or st.get('role')} "
            f"（代号 {st.get('role')}）\n"
            f"- 承担模型：{st.get('display_name') or _display_name(st.get('ai_id', ''))} "
            f"(ai_id={st.get('ai_id')})"
        )
        extra = []
        if st.get("duration_ms"):
            extra.append(f"耗时 {int(st['duration_ms'])}ms")
        if st.get("word_count"):
            extra.append(f"字数 {st['word_count']}")
        if st.get("status"):
            extra.append(f"状态 {st['status']}")
        if extra:
            head += "\n- " + "，".join(extra)
        output = (st.get("output") or "").strip()
        snippet = output[:_STAGE_SNIPPET]
        if len(output) > _STAGE_SNIPPET:
            snippet += "\n…（后续内容已省略）"
        parts.append(f"{head}\n\n产出内容：\n{snippet or '（无产出）'}")
    return "\n\n".join(parts)


async def inspect_quality(
    *,
    feature: str,
    task_id: int,
    requirement: str,
    stages: List[Dict[str, Any]],
    stream_id: str,
    judge_ai: Optional[str] = None,
) -> Dict[str, Any]:
    """
    给流水线各环节 AI 打质检分。

    返回 {overall, scores:[{role, role_label, ai_id, display_name, score, grade, comment}],
          summary, scored_by}。
    质检失败时按「成功即及格」兜底打分，保证前端总能拿到一份榜单。
    """
    valid_stages = [s for s in (stages or []) if s.get("ai_id")]
    if not valid_stages:
        return {"overall": 0, "scores": [], "summary": "", "scored_by": ""}

    label, pipeline_desc = FEATURE_LABELS.get(
        feature, (feature, " → ".join(s.get("role", "") for s in valid_stages)))

    judge_cfg = DISCUSSION_JUDGES["evaluation"]
    judge_model = judge_ai or judge_cfg["ai_id"]
    if judge_model not in AI_MODELS:
        judge_model = judge_cfg["ai_id"]

    system = build_prompt(
        QUALITY_INSPECTOR_SYSTEM,
        feature_label=label,
        pipeline_desc=pipeline_desc,
        stage_count=len(valid_stages),
    )
    user = build_prompt(
        QUALITY_INSPECTOR_USER,
        requirement=(requirement or "（无）")[:800],
        stages_text=_build_stages_text(valid_stages),
    )

    result = await call_ai(
        judge_model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        task_type="judge",
        task_id=task_id,
        fallback=judge_cfg.get("fallback", []),
        max_tokens=1500,
        temperature=0.3,
    )

    parsed = _extract_json(result.content) if result.status == "success" else None

    # ---- 组装分数 ----
    raw_scores: List[Dict[str, Any]] = []
    if isinstance(parsed, dict):
        raw_scores = [
            s for s in (parsed.get("scores") or [])
            if isinstance(s, dict) and s.get("role")
        ]

    by_role = {str(s.get("role")): s for s in raw_scores}
    overall = _to_score(
        (parsed or {}).get("overall") if isinstance(parsed, dict) else None,
        default=0.0,
    )

    scored: List[Dict[str, Any]] = []
    for st in valid_stages:
        role = str(st.get("role"))
        hit = by_role.get(role)
        # 质检没给分 → 用整体分兜底；整体分也没有 → 该环节成功给 70（及格线）
        fallback_score = overall if overall > 0 else (
            70.0 if (st.get("status") != "failed" and st.get("output")) else 0.0)
        score = _to_score((hit or {}).get("score"), fallback_score)
        comment = str((hit or {}).get("comment") or "").strip()
        grade = str((hit or {}).get("grade") or "").strip().upper() or _grade_of(score)
        if grade not in ("S", "A", "B", "C"):
            grade = _grade_of(score)
        scored.append({
            "role": role,
            "role_label": st.get("role_label") or st.get("role"),
            "ai_id": st.get("ai_id"),
            "display_name": st.get("display_name") or _display_name(st.get("ai_id", "")),
            "score": round(score, 1),
            "grade": grade,
            "comment": comment or ("本环节产出已交付，质检未给出具体评语"),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    summary = str((parsed or {}).get("summary") or "").strip()
    if not summary:
        summary = "质检员未返回总结" if result.status != "success" else ""

    # ---- 落库（覆盖写，重跑不产生两份榜）----
    records = [{
        "feature": feature,
        "task_id": task_id,
        "ai_id": s["ai_id"],
        "display_name": s["display_name"],
        "role": s["role_label"] or s["role"],
        "score": s["score"],
        "grade": s["grade"],
        "comment": s["comment"],
        "scored_by": judge_model,
    } for s in scored]
    try:
        await asyncio.to_thread(
            score_repo.replace_task_scores_sync, feature, task_id, records,
            round(overall, 1), summary)
    except Exception:  # noqa: BLE001
        logger.exception("质检分落库失败: feature=%s task=%s", feature, task_id)

    payload = {
        "feature": feature,
        "task_id": task_id,
        "overall": round(overall, 1),
        "scores": scored,
        "summary": summary,
        "scored_by": judge_model,
    }
    await sse_manager.publish(stream_id, "quality_scores", payload)
    logger.info(
        "质检完成: feature=%s task=%s judge=%s scores=%s",
        feature, task_id, judge_model,
        [(s["display_name"], s["score"]) for s in scored],
    )
    return payload
