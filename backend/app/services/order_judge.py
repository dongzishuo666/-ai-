# ============================================================
# 文件：backend/app/services/order_judge.py
# 说明：秩序裁判。每轮结束后触发，批量输出全部 AI 的四维分数 + 轮次总结。
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config.business import (
    DISCUSSION_CONFIG,
    DISCUSSION_JUDGES,
    SCORING_WEIGHTS,
)
from app.config.prompts import (
    DISC_CONSENSUS_SUMMARY_REQ,
    DISC_JUDGE_MULTI_ROLE_GUARD,
    DISC_ORDER_JUDGE_SYSTEM,
    DISC_ORDER_JUDGE_USER,
    TEAM_AWARENESS,
    TEAM_ROLE_JUDGE_ORDER,
    build_prompt,
)
from app.services.ai_scheduler import AICallResult, call_ai

logger = logging.getLogger("app.order_judge")


_ORDER_JUDGE_AI = DISCUSSION_JUDGES["order"]["ai_id"]              # 'zhipu'
_ORDER_FALLBACK = DISCUSSION_JUDGES["order"].get("fallback", [])

# 单轮评分硬超时（秒）。
# 单个模型最坏耗时 = AI_CALL_TIMEOUT(45s) × (retry_count+1=3) + 退避，
# 再串上降级链可达数分钟；这段时间前端零输出，表现为"讨论卡住"。
# 超时后直接走规则兜底，保证流水线继续推进。
_ROUND_SCORE_TIMEOUT_SECONDS = 90


def _extract_json(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _clamp_score(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, v))


def _weighted(quality: float, relevance: float, contribution: float, compliance: float) -> float:
    return round(
        quality * SCORING_WEIGHTS["quality"]
        + relevance * SCORING_WEIGHTS["relevance"]
        + contribution * SCORING_WEIGHTS["contribution"]
        + compliance * SCORING_WEIGHTS["compliance"],
        1,
    )


async def score_round(
    *,
    discussion_id: int,
    topic: str,
    round_no: int,
    total_rounds: int,
    round_messages: List[Dict[str, Any]],            # 本轮全部消息（含合规判定结果）
    compliance_results: List[Dict[str, Any]],         # 每条消息的合规判定
    current_scores: Dict[str, float],                 # 当前累计分 {ai_id: total}
    all_discussants: List[Dict[str, Any]],            # 全部 discussant（3 条）
    active_ai_ids: Optional[List[str]] = None,        # 本轮实际发言的 AI；未发言者标记 absent，不计分
    judge_ai: Optional[str] = None,                   # 本场指定的秩序裁判；缺省回落配置默认值
    until_consensus: bool = False,                    # 「直到共识」模式：总结要写共同点+分歧
) -> Dict[str, Any]:
    """
    返回：
    {
      "scores": [{ai_id, display_name, quality, relevance, contribution, compliance,
                  weighted_total, absent}],
      "round_summary": "..."
    }
    - 未发言（调用失败/被禁言）的 AI 标记 absent=True，权重分记 0 但不参与排名与禁言判定，
      避免"离线 AI 拿 0 分"污染计分榜。
    - 裁判调用失败时，本轮发言者给 0 分兜底，不中断讨论。
    """
    # 本轮实际发言者（未指定时按 round_messages 推断）
    active_set = set(active_ai_ids) if active_ai_ids is not None else {
        m["ai_id"] for m in round_messages
    }
    active_names = {
        m.get("display_name") for m in round_messages if m.get("display_name")
    }

    # 席位映射：同一个模型可能占多个席位，身份键必须用席位名而非 ai_id，
    # 否则两个同源席位会被当成同一个人、共用一份评分。
    seat_by_name = {p["display_name"]: p for p in (all_discussants or [])}
    names_by_ai: Dict[str, List[str]] = {}
    for p in (all_discussants or []):
        names_by_ai.setdefault(p["ai_id"], []).append(p["display_name"])

    def _is_absent(p: Dict[str, Any]) -> bool:
        """按席位判断是否缺席；拿不到席位信息时回退按 ai_id 判断。"""
        if active_names and p.get("display_name"):
            return p["display_name"] not in active_names
        return p["ai_id"] not in active_set

    # 组装本轮发言文本（用席位名，保证同源席位可被区分）
    lines = []
    for m in round_messages:
        seat_name = m.get("display_name") or m.get("ai_id")
        lines.append(
            f"[{seat_name}] 第{m['message_order']}位: {m['content'][:600]}"
        )
    round_messages_text = "\n\n".join(lines) if lines else "（本轮无发言）"

    compliance_text = "\n".join(
        f"- {c.get('display_name') or c['ai_id']}: {c['status']}"
        for c in compliance_results
    ) or "（无）"

    scores_text = "\n".join(
        f"- {name}: {score}"
        for name, score in current_scores.items()
    ) or "（无）"

    # 共识模式下要求总结同时写「已达成」与「仍分歧」：
    # 共识判定只读轮次总结，若总结只写分歧，裁判永远判不出共识。
    summary_requirement = DISC_CONSENSUS_SUMMARY_REQ if until_consensus else ""

    user = build_prompt(
        DISC_ORDER_JUDGE_USER,
        topic=topic,
        round=round_no,
        total_rounds=total_rounds,
        round_messages=round_messages_text,
        compliance_results=compliance_text,
        current_scores=scores_text,
        summary_requirement=summary_requirement,
    )
    # 团队意识：评分与总结要覆盖全部席位，不能只盯着某一个席位写（避免一言堂）
    system = DISC_ORDER_JUDGE_SYSTEM + build_prompt(
        TEAM_AWARENESS,
        role_line=TEAM_ROLE_JUDGE_ORDER,
        peers_line="协作席位：本场全部讨论者席位与「合规裁判」席位；"
                   "结果交给终局评价环节。",
    )
    # 秩序裁判兼任讨论者时注入身份锚定：防止给自己打分偏高
    discussant_ids = {
        m.get("ai_id") for m in (all_discussants or []) if m.get("ai_id")
    }
    if (judge_ai or _ORDER_JUDGE_AI) in discussant_ids:
        system += "\n\n" + build_prompt(
            DISC_JUDGE_MULTI_ROLE_GUARD,
            role_label="秩序裁判",
            other_roles="本场讨论者席位",
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # 使用本场指定的秩序裁判（用户自选 / 智能组队），缺省回落配置默认值
    judge_model = judge_ai or _ORDER_JUDGE_AI
    try:
        # 超时覆盖整个「主模型重试 + 降级链」，避免裁判侧限流时整场讨论静默卡死
        result = await asyncio.wait_for(
            call_ai(
                judge_model,
                messages,
                task_type="judge",
                task_id=discussion_id,
                fallback=_ORDER_FALLBACK,
                max_tokens=2048,
                temperature=0.3,
            ),
            timeout=_ROUND_SCORE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "秩序裁判评分超时（>%ss），本轮改用规则兜底: discussion=%s round=%s judge=%s",
            _ROUND_SCORE_TIMEOUT_SECONDS, discussion_id, round_no, judge_model,
        )
        result = AICallResult(
            ai_id=judge_model,
            status="timeout",
            error_message=f"评分超时（>{_ROUND_SCORE_TIMEOUT_SECONDS}s）",
        )

    parsed = _extract_json(result.content) if result.status == "success" else None

    # 兜底：AI 失败或无法解析 → 全部 AI 给 0 分
    if parsed is None:
        logger.warning(
            "秩序裁判失败/无法解析，全部 AI 本轮 0 分: discussion=%s round=%s",
            discussion_id, round_no,
        )
        fallback_scores = [
            {
                "ai_id": p["ai_id"],
                "display_name": p["display_name"],
                "seat_id": p.get("id"),
                "quality": 0.0, "relevance": 0.0,
                "contribution": 0.0, "compliance": 0.0,
                "weighted_total": 0.0,
                "absent": _is_absent(p),
            }
            for p in all_discussants
        ]
        return {"scores": fallback_scores, "round_summary": "（评分失败，本轮计0分）"}

    # 解析 AI 返回的 scores，映射到席位全集（缺则 0 分）
    # 优先用 seat（席位名）；旧的 ai_id 格式仅在该 ai_id 唯一对应一个席位时才接受。
    seat_scores_map: Dict[str, Dict[str, Any]] = {}
    for item in parsed.get("scores", []) or []:
        name = item.get("seat")
        if not name and item.get("ai_id"):
            candidates = names_by_ai.get(item["ai_id"]) or []
            name = candidates[0] if len(candidates) == 1 else None
        if not name or name not in seat_by_name:
            continue
        q = _clamp_score(item.get("quality"))
        r = _clamp_score(item.get("relevance"))
        c = _clamp_score(item.get("contribution"))
        comp = _clamp_score(item.get("compliance"))
        seat_scores_map[name] = {
            "quality": q, "relevance": r,
            "contribution": c, "compliance": comp,
            "weighted_total": _weighted(q, r, c, comp),
        }

    final_scores: List[Dict[str, Any]] = []
    for p in all_discussants:
        name = p["display_name"]
        s = seat_scores_map.get(name, {
            "quality": 0.0, "relevance": 0.0,
            "contribution": 0.0, "compliance": 0.0,
            "weighted_total": 0.0,
        })
        final_scores.append({
            "ai_id": p["ai_id"],
            "display_name": name,
            "seat_id": p.get("id"),
            "absent": _is_absent(p),
            **s,
        })

    round_summary = str(parsed.get("round_summary", "")).strip() or "（本轮无总结）"

    return {"scores": final_scores, "round_summary": round_summary}
