# ============================================================
# 文件：backend/app/services/discussion_engine.py
# 说明：讨论主引擎。多轮发言循环 + 裁判调度 + 禁言 + 暂停/恢复。
# 事件顺序严格按蓝图 2.2：
#   stream_start → round_start → (ai_message+compliance_result)*n
#     → round_score → scoreboard_update → [ban_action] → round_end
#   → ... → discussion_end → judge_evaluation → done
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.config import (
    PAUSE_TIMEOUT_SECONDS,
    REASONING_MODELS,
    REASONING_MODEL_MAX_TOKENS,
)
from app.config.business import (
    CONSENSUS_CONFIG,
    DISCUSSION_CONFIG,
    DISCUSSION_JUDGES,
    DISCUSSION_PARTICIPANTS,
    SCORING_THRESHOLD,
    SCORING_WEIGHTS,
)
from app.config.prompts import (
    DISC_CONSENSUS_CHECK,
    DISC_CONSENSUS_ROUND_PRESSURE,
    DISC_CONSENSUS_SPEAKER_SYSTEM,
    DISC_CONSENSUS_SPEAKER_USER,
    DISC_CONSENSUS_SUMMARY_REQ,
    DISC_MIDTERM_BLOCK,
    DISC_MIDTERM_SUMMARY_SYSTEM,
    DISC_MIDTERM_SUMMARY_USER,
    DISC_MEMORY_BLOCK,
    TEAM_AWARENESS,
    TEAM_ROLE_DISCUSSANT,
    DISC_STYLE_AFFECTIVE,
    DISC_STYLE_PLANNING,
    DISC_STYLE_ROLEPLAY,
    DISC_DISCUSSANT_SYSTEM,
    DISC_DISCUSSANT_USER,
    DISC_JUDGE_MULTI_ROLE_GUARD,
    DISC_MULTI_SEAT_GUARD,
    DISC_EVALUATION_JUDGE_SYSTEM,
    DISC_EVALUATION_JUDGE_USER,
    TEAM_ROLE_JUDGE_EVALUATION,
    build_prompt,
)
from app.database import discussion_repo
from app.database import extension_repo
from app.database import score_repo
from app.services.ai_scheduler import call_ai
from app.services.compliance_judge import check_compliance
from app.services.order_judge import score_round
from app.services.sse_manager import sse_manager

logger = logging.getLogger("app.discussion_engine")


# 角色扮演模式下按发言顺序分配的角色（本场固定 3 位讨论者，正好一角色一人）
_ROLEPLAY_ROLES: tuple = ("正方辩手", "反方辩手", "中立主持人")


# ============================================================
# 暂停/恢复：内存态（单 worker）
# ============================================================

# discussion_id → asyncio.Event（未 set = 暂停中）
_paused_events: Dict[int, asyncio.Event] = {}
# discussion_id → watchdog task
_pause_watchdogs: Dict[int, asyncio.Task] = {}
# discussion_id → 已触发暂停超时（供主流水线恢复阻塞后感知）
_pause_timeouts: set[int] = set()


def is_paused(discussion_id: int) -> bool:
    return discussion_id in _paused_events


def mark_paused(discussion_id: int) -> None:
    _paused_events[discussion_id] = asyncio.Event()


def mark_resumed(discussion_id: int) -> None:
    _pause_timeouts.discard(discussion_id)
    ev = _paused_events.pop(discussion_id, None)
    if ev:
        ev.set()
    wd = _pause_watchdogs.pop(discussion_id, None)
    if wd and not wd.done():
        wd.cancel()


async def _wait_if_paused(discussion_id: int) -> None:
    """
    在检查点调用：若暂停中，阻塞直到恢复。
    恢复后若期间触发过暂停超时，抛出 _PauseTimeout 让主流水线终止讨论。
    """
    ev = _paused_events.get(discussion_id)
    if ev is not None:
        await ev.wait()
    if discussion_id in _pause_timeouts:
        _pause_timeouts.discard(discussion_id)
        raise _PauseTimeout()


async def _pause_watchdog(discussion_id: int, stream_id: str) -> None:
    """暂停超时监控：PAUSE_TIMEOUT_SECONDS 秒后若仍暂停，自动终止。"""
    try:
        await asyncio.sleep(PAUSE_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return
    if discussion_id not in _paused_events:
        return

    logger.warning("讨论 %s 暂停超时，自动终止", discussion_id)
    # 本协程是独立 task，在此 raise 只会被 asyncio 记为 task 异常，
    # 主流水线永远捕获不到。改为打标记 + 唤醒阻塞中的主流水线，
    # 由主流水线在 _wait_if_paused 返回后抛出 _PauseTimeout。
    _pause_timeouts.add(discussion_id)
    ev = _paused_events.pop(discussion_id, None)
    if ev:
        ev.set()
    _pause_watchdogs.pop(discussion_id, None)


class _PauseTimeout(Exception):
    """内部信号：暂停超时，触发终止。"""


# ============================================================
# 文本工具
# ============================================================

def _count_chars(text: str) -> int:
    return len(text or "")


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
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


def _truncate(text: str, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[:n]


# ============================================================
# 单条发言
# ============================================================

async def _run_one_speak(
    *,
    discussion: Dict[str, Any],
    round_no: int,
    order_in_round: int,
    speaker: Dict[str, Any],
    recent_messages: List[Dict[str, Any]],
    stream_id: str,
    converge_phase: bool = False,   # 收敛期（局中总结之后）→ 改用收敛型 prompt
    midterm_block: str = "",        # 评价裁判的局中总结（收敛期注入）
    teammates: str = "",            # 本轮在场席位名（逗号分隔），用于团队意识注入
) -> Optional[Dict[str, Any]]:
    """
    执行一位 AI 的发言：
      1. 调用 AI
      2. 落库 message
      3. 推 ai_message
      4. 合规判定 → 落库 + 推 compliance_result
    返回落库后的消息 dict（供 order_judge 使用），失败返回 None。
    """
    discussion_id = discussion["discussion_id"]
    topic = discussion["topic"]
    total_rounds = discussion["total_rounds"]
    ai_id = speaker["ai_id"]
    display_name = speaker["display_name"]

    context_window = DISCUSSION_CONFIG["context_window_messages"]
    recent_lines = "\n\n".join(
        f"[{m.get('display_name', m.get('ai_id'))}]: {_truncate(m.get('content', ''), 500)}"
        for m in recent_messages[-context_window:]
    ) or "（暂无历史发言）"

    # 共识模式换用收敛导向的 system：普通版强制「必须推进 / 禁止重复 / 鼓励新角度」，
    # 与达成共识直接冲突（每轮被迫造新概念 → 观点永远在演化 → 永远判不出共识）。
    until_consensus = bool(discussion.get("until_consensus"))
    min_check_round = int(CONSENSUS_CONFIG["min_check_round"])
    # 共识模式分两段：发散期（前 diverge_rounds 轮）沿用原推进式 prompt 求新求深，
    # 收敛期（评价裁判给出局中总结之后）才切到收敛型 prompt。
    if until_consensus and converge_phase:
        system = build_prompt(
            DISC_CONSENSUS_SPEAKER_SYSTEM,
            display_name=display_name,
            context_window=context_window,
            min_check_round=min_check_round,
        )
    else:
        system = build_prompt(
            DISC_DISCUSSANT_SYSTEM,
            display_name=display_name,
            context_window=context_window,
        )

    # ---- 团队意识：让每个席位知道自己在多AI协作系统里的位置 ----
    # 原 prompt 只讲"你怎么发言"，模型并不知道整体是团队协作，容易出现：
    # 把自身局部观点说成最终定论、越界替裁判/其他席位拍板、或强行统一口径。
    peers_line = ""
    if teammates:
        seat_list = [x.strip() for x in teammates.split(",") if x.strip()]
        peers_line = (
            f"本场讨论共 {len(seat_list)} 个席位：{'、'.join(seat_list)}；"
            "你是其中一票，不代表全场。"
        )
    system += "\n\n" + build_prompt(
        TEAM_AWARENESS,
        role_line=build_prompt(TEAM_ROLE_DISCUSSANT, display_name=display_name),
        peers_line=peers_line,
    )

    # ---- 扩展V4：风格模式 Prompt 注入 ----
    style_mode = (discussion.get("style_mode") or "free")
    if style_mode == "planning":
        system += "\n\n" + build_prompt(
            DISC_STYLE_PLANNING, display_name=display_name)
    elif style_mode == "affective":
        system += "\n\n" + build_prompt(DISC_STYLE_AFFECTIVE)
    elif style_mode == "roleplay":
        # 按本轮发言顺序分配角色：1→正方辩手 2→反方辩手 3→中立主持人
        role_name = _ROLEPLAY_ROLES[(order_in_round - 1) % len(_ROLEPLAY_ROLES)]
        system += "\n\n" + build_prompt(
            DISC_STYLE_ROLEPLAY, role_name=role_name, topic=topic)

    # ---- 扩展V4：认知框架记忆注入（相关性自判） ----
    if discussion.get("memory_enabled"):
        frame = await asyncio.to_thread(extension_repo.get_active_frame_sync)
        if frame:
            system += "\n\n" + build_prompt(
                DISC_MEMORY_BLOCK, frame_text=frame["frame_text"][:2000])

    # ---- 一AI多职：席位锚定 ----
    # 同一个模型可能被用户安排在多个席位（含兼任裁判），不加约束会出现
    # 替其他席位发言 / 同源席位互相附和 / 把裁判视角带回讨论。
    seat_rows = await asyncio.to_thread(
        discussion_repo.get_participants_sync, discussion_id
    )
    seat_map: Dict[str, int] = {}
    discussant_ids: List[str] = []
    compliance_judge_model: Optional[str] = None
    for r in seat_rows or []:
        seat_map[r["ai_id"]] = seat_map.get(r["ai_id"], 0) + 1
        if r.get("role") == "discussant":
            discussant_ids.append(r["ai_id"])
        elif r.get("role") == "judge" and r.get("judge_type") == "compliance":
            compliance_judge_model = r["ai_id"]

    if seat_map.get(ai_id, 0) > 1:
        system += "\n\n" + build_prompt(
            DISC_MULTI_SEAT_GUARD, display_name=display_name)

    if until_consensus and converge_phase:
        # 收敛期每轮都注入收敛压力，压缩"开新战场"的冲动
        converge_start = int(CONSENSUS_CONFIG.get("diverge_rounds", 3)) + 1
        convergence_block = build_prompt(
            DISC_CONSENSUS_ROUND_PRESSURE,
            round=round_no,
            min_check_round=converge_start,
        )
        user = build_prompt(
            DISC_CONSENSUS_SPEAKER_USER,
            topic=topic,
            round=round_no,
            total_rounds=total_rounds,
            recent_messages=recent_lines,
            order=order_in_round,
            midterm_block=midterm_block or "",
            convergence_block=convergence_block,
        )
    else:
        user = build_prompt(
            DISC_DISCUSSANT_USER,
            topic=topic,
            round=round_no,
            total_rounds=total_rounds,
            recent_messages=recent_lines,
            order=order_in_round,
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    result = await call_ai(
        ai_id,
        messages,
        task_type="discussion",
        task_id=discussion_id,
        fallback=None,   # 讨论者不降级（避免身份混乱）
        # 推理模型（kimi-k2.6）会把 token 花在 reasoning 上，
        # 给 1024 会导致正文只剩几个字、被合规判成空发言
        max_tokens=(
            REASONING_MODEL_MAX_TOKENS if ai_id in REASONING_MODELS else 1024
        ),
        temperature=0.8,
    )

    if result.status != "success":
        logger.warning("AI %s 发言失败: %s", ai_id, result.error_message)
        return None

    content = (result.content or "").strip()
    if len(content) < DISCUSSION_CONFIG["min_message_length"]:
        # 长度不足：仍落库，但合规判定会处理
        pass
    content = _truncate(content, DISCUSSION_CONFIG["max_message_length"])

    word_count = _count_chars(content)
    message_id = await asyncio.to_thread(
        discussion_repo.insert_message_sync,
        discussion_id, round_no, order_in_round, ai_id, display_name,
        content, word_count,
    )

    # 推 ai_message
    await sse_manager.publish(stream_id, "ai_message", {
        "message_id": message_id,
        "round": round_no,
        "order": order_in_round,
        "ai_id": ai_id,
        "display_name": display_name,
        "content": content,
        "word_count": word_count,
        "timestamp": None,   # 前端可忽略
    })

    # 合规判定
    compliance = await check_compliance(
        discussion_id=discussion_id,
        topic=topic,
        round_no=round_no,
        speaker_ai_id=ai_id,
        speaker_display_name=display_name,
        content=content,
        recent_3_messages=recent_messages[-3:],
        judge_ai=compliance_judge_model,
        discussant_ids=discussant_ids,
    )

    await asyncio.to_thread(
        discussion_repo.update_message_compliance_sync,
        message_id,
        compliance["status"],
        compliance.get("reason"),
        compliance.get("violation_type"),
    )

    compliance_event = {
        "message_id": message_id,
        "ai_id": ai_id,
        "status": compliance["status"],
    }
    if compliance["status"] == "violation":
        compliance_event["violation_type"] = compliance.get("violation_type")
        compliance_event["reason"] = compliance.get("reason")
        compliance_event["action"] = compliance.get("action", "warning")
    await sse_manager.publish(stream_id, "compliance_result", compliance_event)

    # 合规裁判日志
    await asyncio.to_thread(
        discussion_repo.insert_judge_log_sync,
        discussion_id, "compliance",
        # 记本场实际生效的裁判（弹劾换人后会变），而不是配置里的默认值
        compliance_judge_model or DISCUSSION_JUDGES["compliance"]["ai_id"],
        "compliance_check",
        ai_id,
        json.dumps(compliance, ensure_ascii=False),
        round_no,
    )

    return {
        "message_id": message_id,
        "ai_id": ai_id,
        "display_name": display_name,
        "message_order": order_in_round,
        "content": content,
        "word_count": word_count,
        "compliance_status": compliance["status"],
        "compliance_result": compliance,
        "created_at": None,
    }


# ============================================================
# 禁言判定（后端规则引擎）
# ============================================================

async def _compute_ban_actions(
    discussion_id: int, round_no: int, participants: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    裁定问题3：第1轮仅记录，第2轮起判定。
    规则：
      条件A：连续2轮排名末位 且 综合分 < ban_score_threshold
      条件B：本轮合规判定为 violation
    保护：min_active_participants = 3
    """
    if round_no < SCORING_THRESHOLD["ban_consecutive_rounds"]:
        return []

    # 活跃讨论者数量保护
    active_count = sum(1 for p in participants if not p["is_banned"])
    if active_count <= SCORING_THRESHOLD["min_active_participants"]:
        return []

    # 本轮有评分记录者（缺席/未发言者不写入 round_scores，也不参与末位判定，
    # 否则"离线 AI 0 分"会被误判为末位而禁言）
    scored_rows = await asyncio.to_thread(
        discussion_repo.get_round_scores_sync, discussion_id, round_no,
    )
    # 以席位为键：同一模型的多席位必须分别判定（seat_id 缺失时回退 display_name）
    scored_ids = {r.get("seat_id") or r["display_name"] for r in scored_rows}
    if not scored_ids:
        return []

    actions: List[Dict[str, Any]] = []

    for p in participants:
        if p["is_banned"]:
            continue  # 已禁言中，不重复禁言
        ai_id = p["ai_id"]
        seat_key = p.get("id") or p["display_name"]
        if seat_key not in scored_ids:
            continue  # 本轮未发言，不参与禁言判定

        # 条件B：本轮有 violation（按席位判定，避免同源席位连带）
        has_violation = await asyncio.to_thread(
            _has_violation_in_round_sync, discussion_id, round_no, ai_id,
            p.get("display_name"),
        )
        if has_violation:
            actions.append({
                "ai_id": ai_id,
                "seat_id": p.get("id"),
                "display_name": p["display_name"],
                "ban_type": "violation",
                "reason": f"第{round_no}轮发言违反合规规则",
            })
            continue

        # 条件A：连续2轮末位 且 综合分低于阈值
        # 检查最近 ban_consecutive_rounds 轮的排名
        consecutive_last = True
        for back in range(SCORING_THRESHOLD["ban_consecutive_rounds"]):
            r = round_no - back
            if r < 1:
                consecutive_last = False
                break
            # 用「该轮实际计分人数」当分母：历史轮有缺席者时人数更少，
            # 拿本轮人数去比会让末位永远判不出来
            rank, round_scored_count = await asyncio.to_thread(
                discussion_repo.compute_rank_in_round_sync,
                discussion_id, r, ai_id, p.get("id"),
            )
            if rank is None or rank != round_scored_count:
                consecutive_last = False
                break
        if not consecutive_last:
            continue

        # 综合分（累计 / 已完成轮次）
        completed = round_no
        avg = float(p["total_score"] or 0.0) / max(1, completed)
        if avg < SCORING_THRESHOLD["ban_score_threshold"]:
            actions.append({
                "ai_id": ai_id,
                "seat_id": p.get("id"),
                "display_name": p["display_name"],
                "ban_type": "low_score",
                "reason": (
                    f"连续{SCORING_THRESHOLD['ban_consecutive_rounds']}轮排名末位"
                    f"且综合分低于{SCORING_THRESHOLD['ban_score_threshold']}"
                ),
            })

    return actions


async def _execute_ban_action(
    stream_id: str, discussion_id: int, ba: Dict[str, Any],
    round_no: int, order_judge_model: str,
) -> int:
    """
    执行一条禁言：按席位落库 + 写禁言记录 / 裁判日志 + 推 SSE。

    违规（violation）与低分（low_score）用各自的禁言轮数配置，
    不再一律套 low_score_ban_rounds——否则 violation_ban_rounds 是死配置。
    返回实际执行的禁言轮数。
    """
    ban_rounds = (
        SCORING_THRESHOLD["violation_ban_rounds"]
        if ba["ban_type"] == "violation"
        else SCORING_THRESHOLD["low_score_ban_rounds"]
    )
    # 按席位禁言：同源的其他席位不受牵连
    await asyncio.to_thread(
        discussion_repo.set_seat_banned_sync,
        discussion_id, ba.get("seat_id"), True,
        round_no + 1, round_no + ban_rounds,
        ai_id=ba["ai_id"],
    )
    await asyncio.to_thread(
        discussion_repo.insert_ban_record_sync,
        discussion_id, ba["ai_id"], ba["display_name"],
        "banned", ba["reason"], ba["ban_type"],
        round_no + 1, ban_rounds, True,
        seat_id=ba.get("seat_id"),
    )
    await asyncio.to_thread(
        discussion_repo.insert_judge_log_sync,
        discussion_id, "order", order_judge_model,
        "ban_execute", ba["ai_id"],
        json.dumps({**ba, "ban_rounds": ban_rounds},
                   ensure_ascii=False), round_no,
    )
    await sse_manager.publish(stream_id, "ban_action", {
        "ai_id": ba["ai_id"],
        "display_name": ba["display_name"],
        "seat_id": ba.get("seat_id"),
        "action": "banned",
        "reason": ba["reason"],
        "ban_type": ba["ban_type"],
        "ban_rounds": ban_rounds,
        "effective_from_round": round_no + 1,
        "current_round": round_no,
    })
    return ban_rounds


def _has_violation_in_round_sync(
    discussion_id: int, round_no: int, ai_id: str,
    display_name: Optional[str] = None,
) -> bool:
    """
    检查某席位在某轮是否有 violation。

    传入 display_name 时按席位判定：同一模型占多个席位时，
    按 ai_id 判定会让一个席位违规连带判定同源席位也违规。
    """
    from app.database.connection import get_connection
    with get_connection() as conn:
        if display_name:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM discussion_messages
                WHERE discussion_id = ? AND round = ? AND display_name = ?
                  AND compliance_status = 'violation'
                """,
                (discussion_id, round_no, display_name),
            ).fetchone()
            return int(row["c"]) > 0 if row else False
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM discussion_messages
            WHERE discussion_id = ? AND round = ? AND ai_id = ?
              AND compliance_status = 'violation'
            """,
            (discussion_id, round_no, ai_id),
        ).fetchone()
    return bool(row and row["c"] > 0)


# ============================================================
# 评价裁判（终局）
# ============================================================

def _grade_of(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    return "C"


async def _save_final_scores(
    *,
    discussion_id: int,
    parsed: Optional[Dict[str, Any]],
    judge_model: str,
    rounds_completed: int,
    stream_id: str,
) -> None:
    """
    最终积分榜：把终局评价裁判给每个席位的 final_score 落库，并推 final_scores 事件。

    落库到 ai_score_records（feature=discussion），长期累积即该 AI 的「平时分」，
    用户在下次创建讨论时可以看到，用来辅助选人。
    裁判没给分时用轮次平均分兜底，保证每场讨论都有一份最终榜。
    """
    leaderboard = await asyncio.to_thread(
        discussion_repo.get_leaderboard_sync, discussion_id)
    if not leaderboard:
        return

    ranking = (parsed or {}).get("contribution_ranking") or []
    by_name: Dict[str, Dict[str, Any]] = {}
    for r in ranking:
        if isinstance(r, dict):
            key = str(r.get("display_name") or r.get("seat") or "").strip()
            if key:
                by_name[key] = r

    scored: List[Dict[str, Any]] = []
    for item in leaderboard:
        name = str(item.get("display_name") or "")
        hit = by_name.get(name) or {}
        absent = bool(item.get("absent"))

        if absent:
            score = 0.0
        else:
            raw = hit.get("final_score")
            try:
                score = float(raw)
            except (TypeError, ValueError):
                # 裁判漏给分 → 用轮次平均分兜底（量纲与 0-100 一致）
                total = float(item.get("total_score") or 0)
                score = (total / rounds_completed) if rounds_completed > 0 else 60.0
        score = max(0.0, min(100.0, score))

        grade = str(hit.get("grade") or "").strip().upper()
        if grade not in ("S", "A", "B", "C"):
            grade = _grade_of(score)
        comment = str(hit.get("comment") or hit.get("contribution") or "").strip()
        if absent:
            comment = comment or "本场未发言（缺席）"
        elif not comment:
            comment = "裁判未给出评语，按轮次表现折算"

        scored.append({
            "ai_id": item.get("ai_id"),
            "display_name": name,
            "role": "discussant",
            "score": round(score, 1),
            "grade": grade,
            "comment": comment[:200],
            "seat": str(hit.get("seat") or name),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    records = [{
        "feature": "discussion",
        "task_id": discussion_id,
        "ai_id": s["ai_id"],
        "display_name": s["display_name"],
        "role": "discussant",
        "score": s["score"],
        "grade": s["grade"],
        "comment": s["comment"],
        "scored_by": judge_model,
    } for s in scored]
    try:
        await asyncio.to_thread(
            score_repo.replace_task_scores_sync, "discussion", discussion_id, records)
    except Exception:  # noqa: BLE001
        logger.exception("最终积分榜落库失败: discussion=%s", discussion_id)

    await sse_manager.publish(stream_id, "final_scores", {
        "discussion_id": discussion_id,
        "scores": scored,
        "scored_by": judge_model,
    })


async def _run_evaluation_judge(
    *, discussion: Dict[str, Any], stream_id: str,
) -> None:
    discussion_id = discussion["discussion_id"]
    topic = discussion["topic"]
    judge_cfg = DISCUSSION_JUDGES["evaluation"]

    # 全部消息摘要（压缩）
    all_messages = await asyncio.to_thread(
        discussion_repo.get_all_messages_sync, discussion_id
    )
    summaries = await asyncio.to_thread(
        discussion_repo.get_round_summaries_sync, discussion_id
    )
    rounds_text = "\n\n".join(
        f"第{s['round']}轮: {s['summary_text']}"
        for s in summaries
    ) or "（无轮次总结）"

    leaderboard = await asyncio.to_thread(
        discussion_repo.get_leaderboard_sync, discussion_id
    )
    scores_text = "\n".join(
        f"- {item['display_name']} ({item['ai_id']}): {item['total_score']}"
        for item in leaderboard
    ) or "（无评分）"

    bans = await asyncio.to_thread(
        discussion_repo.get_ban_history_sync, discussion_id
    )
    ban_summary = "\n".join(
        f"- {b['display_name']} {b['action']} @第{b['effective_round']}轮: {b['reason']}"
        for b in bans
    ) or "（无禁言）"

    user = build_prompt(
        DISC_EVALUATION_JUDGE_USER,
        topic=topic,
        total_rounds=discussion["total_rounds"],
        rounds_completed=discussion["rounds_completed"],
        end_reason=discussion.get("end_reason") or "max_rounds",
        participant_count=len(leaderboard),
        ban_summary=ban_summary,
        all_rounds_summary=rounds_text,
        final_scores=scores_text,
    )
    # 本场 evaluation 裁判（可被用户/智能组队指定），回落到配置默认值
    eval_row = await asyncio.to_thread(
        discussion_repo.get_judge_sync, discussion_id, "evaluation"
    )
    judge_model = (eval_row or {}).get("ai_id") or judge_cfg["ai_id"]

    # 终审裁判兼任讨论者时注入身份锚定：终局评估决定排名，最需要防自我偏袒
    # 团队意识：终局结论必须综合各席位产出，不能只采信一个席位、也不重开讨论
    system = DISC_EVALUATION_JUDGE_SYSTEM + build_prompt(
        TEAM_AWARENESS,
        role_line=TEAM_ROLE_JUDGE_EVALUATION,
        peers_line="协作席位：本场全部讨论者席位，以及「合规裁判」「秩序裁判」两个裁判席位。",
    )
    discussant_rows = await asyncio.to_thread(
        discussion_repo.get_discussants_sync, discussion_id
    )
    if judge_model in {r["ai_id"] for r in (discussant_rows or [])}:
        system += "\n\n" + build_prompt(
            DISC_JUDGE_MULTI_ROLE_GUARD,
            role_label="最终评价裁判",
            other_roles="本场讨论者席位",
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    result = await call_ai(
        judge_model,
        messages,
        task_type="judge",
        task_id=discussion_id,
        fallback=judge_cfg.get("fallback", []),
        max_tokens=3000,
        temperature=0.3,
    )

    parsed = _extract_json(result.content) if result.status == "success" else None

    # 兜底：AI 返回了内容但非标准 JSON 时，用原文作为 summary，避免终局报告完全空白
    if parsed is None and result.status == "success" and (result.content or "").strip():
        parsed = {
            "efficiency_score": 0,
            "consensus_level": "unknown",
            "contribution_ranking": [],
            "unresolved_issues": [],
            "suggestions": [],
            "summary": (result.content or "").strip()[:4000],
        }
        logger.warning(
            "评价裁判输出非 JSON，已按纯文本兜底: discussion=%s judge=%s",
            discussion_id, judge_model,
        )

    if parsed is None:
        logger.warning(
            "评价裁判失败/无法解析: discussion=%s judge=%s err=%s",
            discussion_id, judge_model, result.error_message,
        )
        # 兜底：推空评价 + 空行动方案
        await sse_manager.publish(stream_id, "judge_evaluation", {
            "efficiency_score": 0,
            "consensus_level": "none",
            "contribution_ranking": [],
            "unresolved_issues": [],
            "suggestions": [],
            "summary": "（评价裁判失败，未生成总结）",
        })
        await asyncio.to_thread(
            discussion_repo.update_discussion_judge_summary_sync,
            discussion_id, "{}",
        )
        # 裁判失败时仍按轮次平均分出一份最终榜，避免本场没有终榜可看
        await _save_final_scores(
            discussion_id=discussion_id,
            parsed=None,
            judge_model=judge_model,
            rounds_completed=int(discussion.get("rounds_completed") or 0),
            stream_id=stream_id,
        )
        return

    # 落库
    summary_only = {k: v for k, v in parsed.items() if k != "action_plan"}
    await asyncio.to_thread(
        discussion_repo.update_discussion_judge_summary_sync,
        discussion_id, json.dumps(summary_only, ensure_ascii=False),
    )
    action_plan = str(parsed.get("action_plan", "")).strip()
    if action_plan:
        await asyncio.to_thread(
            discussion_repo.update_discussion_action_plan_sync,
            discussion_id, action_plan,
        )

    # 推 judge_evaluation（不含 action_plan，单独作为 action_plan 事件推）
    eval_event = {k: v for k, v in summary_only.items()}
    await sse_manager.publish(stream_id, "judge_evaluation", eval_event)

    # 最终积分榜：评价裁判给的最终分落库（长期可查 + 累积成平时分）
    await _save_final_scores(
        discussion_id=discussion_id,
        parsed=parsed,
        judge_model=judge_model,
        rounds_completed=int(discussion.get("rounds_completed") or 0),
        stream_id=stream_id,
    )

    # 推 action_plan
    if action_plan:
        await sse_manager.publish(stream_id, "action_plan", {
            "format": "markdown",
            "content": action_plan,
        })

    # 裁判日志
    await asyncio.to_thread(
        discussion_repo.insert_judge_log_sync,
        discussion_id, "evaluation", judge_model,
        "final_evaluation", None,
        json.dumps(eval_event, ensure_ascii=False)[:2000],
        None,
    )


# ============================================================
# 共识检测（扩展V4：until_consensus 模式）
# ============================================================

async def _check_consensus(
    *,
    discussion: Dict[str, Any],
    rounds_completed: int,
    round_summaries: List[str],
    stream_id: str,
    latest_messages: Optional[List[Dict[str, Any]]] = None,
    judge_ai: Optional[str] = None,
) -> Dict[str, Any]:
    """
    第一层「讨论内裁判」判定是否达成共识。
    返回 {"reached": bool, "reason": str, "judge": str}
    - 裁判优先取本场 discussion 的 evaluation 裁判，回落到配置值 + fallback 链
    - 支持严格度（loose / medium / strict）
    """
    discussion_id = discussion["discussion_id"]

    # 裁判选择：本场 evaluation 裁判 → 配置默认
    if not judge_ai:
        judge_row = await asyncio.to_thread(
            discussion_repo.get_judge_sync, discussion_id, "evaluation"
        )
        judge_ai = (judge_row or {}).get("ai_id") or DISCUSSION_JUDGES["evaluation"]["ai_id"]
    judge_cfg = DISCUSSION_JUDGES.get("evaluation", {})

    strictness = str(discussion.get("consensus_strictness") or "medium").lower()
    strictness_desc = CONSENSUS_CONFIG["strictness_desc"].get(
        strictness, CONSENSUS_CONFIG["strictness_desc"]["medium"]
    )

    criteria_map = CONSENSUS_CONFIG.get("criteria", {})
    strictness_criteria = criteria_map.get(strictness, criteria_map.get("medium", ""))

    # 只给裁判看轮次总结不够：总结模板本身引导它写"关键分歧"，
    # 裁判永远只看到分歧。补上本轮实际发言，让它能看到认同/让步信号。
    latest_text = "\n\n".join(
        f"[{m.get('display_name') or m.get('ai_id')}]: "
        f"{_truncate(str(m.get('content') or ''), 500)}"
        for m in (latest_messages or [])
    ) or "（本轮无发言）"

    prompt = build_prompt(
        DISC_CONSENSUS_CHECK,
        topic=discussion["topic"],
        rounds_completed=rounds_completed,
        strictness_desc=strictness_desc,
        strictness_criteria=strictness_criteria,
        latest_messages=latest_text,
        rounds_summary="\n".join(
            f"第{i + 1}轮：{s}" for i, s in enumerate(round_summaries)
        ) or "（暂无）",
    )
    result = await call_ai(
        judge_ai,
        [{"role": "user", "content": prompt}],
        task_type="judge",
        task_id=discussion_id,
        fallback=judge_cfg.get("fallback", []),
        max_tokens=512,
        temperature=0.2,
    )

    if result.status != "success":
        logger.warning(
            "共识判定裁判调用失败（讨论%s 第%s轮）：%s",
            discussion_id, rounds_completed, result.error_message,
        )
        return {"reached": False, "reason": "裁判调用失败，默认继续讨论", "judge": judge_ai}

    content = (result.content or "").strip()

    # 优先解析 JSON {"reached": true/false, "reason": "..."}
    parsed = _extract_json(content)
    if parsed is not None and "reached" in parsed:
        raw = parsed.get("reached")
        if isinstance(raw, str):
            reached = raw.strip().lower() in ("true", "yes", "1", "达成", "已达成共识")
        else:
            reached = bool(raw)
        reason = str(parsed.get("reason", "")).strip() or "（裁判未给出理由）"
    else:
        # 兼容纯文本回答
        reached = "已达成共识" in content and "未达成共识" not in content
        reason = content[:200] or "（裁判未给出理由）"

    logger.info(
        "共识检测（讨论%s，第%s轮后）：reached=%s judge=%s",
        discussion_id, rounds_completed, reached, judge_ai,
    )
    return {"reached": reached, "reason": reason, "judge": judge_ai}


async def _run_midterm_summary(
    *,
    discussion: Dict[str, Any],
    round_summaries: List[str],
    diverge_rounds: int,
    stream_id: str,
) -> str:
    """
    发散期结束后的「局中总结」：由评价裁判把发散成果收拢成可收敛的结构
    （共同点 / 待收敛分歧 / 收敛方向 / 收敛指令），供收敛期注入每位讨论者。

    返回注入文本块；失败时返回空串，讨论照常继续（不阻断流水线）。
    """
    discussion_id = discussion["discussion_id"]
    judge_cfg = DISCUSSION_JUDGES["evaluation"]

    eval_row = await asyncio.to_thread(
        discussion_repo.get_judge_sync, discussion_id, "evaluation"
    )
    judge_model = (eval_row or {}).get("ai_id") or judge_cfg["ai_id"]

    rounds_text = "\n\n".join(
        f"第{i + 1}轮：{s}" for i, s in enumerate(round_summaries)
    ) or "（无轮次总结）"

    system = build_prompt(
        DISC_MIDTERM_SUMMARY_SYSTEM, diverge_rounds=diverge_rounds)
    user = build_prompt(
        DISC_MIDTERM_SUMMARY_USER,
        topic=discussion["topic"],
        rounds_completed=diverge_rounds,
        rounds_summary=rounds_text,
    )
    # 评价裁判兼任讨论者时锚定身份，防止给自己的立场喂偏向性总结
    discussant_rows = await asyncio.to_thread(
        discussion_repo.get_discussants_sync, discussion_id
    )
    if judge_model in {r["ai_id"] for r in (discussant_rows or [])}:
        system += "\n\n" + build_prompt(
            DISC_JUDGE_MULTI_ROLE_GUARD,
            role_label="最终评价裁判",
            other_roles="本场讨论者席位",
        )

    result = await call_ai(
        judge_model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        task_type="judge",
        task_id=discussion_id,
        fallback=judge_cfg.get("fallback", []),
        max_tokens=2048,
        temperature=0.3,
    )

    parsed = _extract_json(result.content) if result.status == "success" else None
    if parsed is None:
        logger.warning(
            "局中总结生成失败（讨论%s）：%s", discussion_id, result.error_message)
        return ""

    agreed_points = parsed.get("agreed_points") or []
    disagreements = parsed.get("core_disagreements") or []
    directions = parsed.get("convergence_directions") or []
    guidance = str(parsed.get("guidance") or "").strip() or "请围绕上述分歧明确表态并收敛。"

    agreed_text = "\n".join(f"- {x}" for x in agreed_points) or "（暂无明确共同点）"
    if disagreements:
        disagree_text = "\n".join(
            "- %s（%s）" % (d.get("point"), d.get("positions"))
            for d in disagreements if isinstance(d, dict)
        ) or "（格式异常）"
    else:
        disagree_text = "（裁判判定已无实质分歧）"
    direction_text = "\n".join(f"- {x}" for x in directions) or "（无）"

    block = build_prompt(
        DISC_MIDTERM_BLOCK,
        diverge_rounds=diverge_rounds,
        agreed_points=agreed_text,
        core_disagreements=disagree_text,
        convergence_directions=direction_text,
        guidance=guidance,
    )

    try:
        await asyncio.to_thread(
            discussion_repo.update_midterm_summary_sync,
            discussion_id,
            json.dumps(parsed, ensure_ascii=False),
        )
    except Exception:  # noqa: BLE001
        logger.exception("局中总结落库失败: discussion=%s", discussion_id)

    await sse_manager.publish(stream_id, "midterm_summary", {
        "round": diverge_rounds,
        "judge": judge_model,
        "agreed_points": agreed_points,
        "core_disagreements": disagreements,
        "convergence_directions": directions,
        "guidance": guidance,
    })
    logger.info("局中总结已生成（讨论%s，第%s轮后）", discussion_id, diverge_rounds)
    return block


# ============================================================
# 主流水线
# ============================================================

async def run_discussion_pipeline(discussion_id: int) -> None:
    stream_id = f"discussion_{discussion_id}"
    pipeline_start = time.monotonic()
    end_reason = "max_rounds"

    try:
        discussion = await asyncio.to_thread(
            discussion_repo.get_discussion_sync, discussion_id
        )
        if discussion is None:
            logger.error("讨论不存在: id=%s", discussion_id)
            return

        participants = await asyncio.to_thread(
            discussion_repo.get_discussants_sync, discussion_id
        )
        # 本场秩序裁判：这里只给个默认值，真正取值放在每轮开头重新查询。
        # （弹劾通过后 participants 表会换成新裁判，若只在流水线启动读一次，
        #   换人要等到下一场讨论才生效，弹劾就成了空转。）
        order_judge_model = DISCUSSION_JUDGES["order"]["ai_id"]
        order_judge_name = order_judge_model
        total_rounds = discussion["total_rounds"]
        topic = discussion["topic"]

        # 「直到共识」模式：达成共识即止，未达成可超过基础轮次，
        # 但不得突破 CONSENSUS_CONFIG["max_rounds"] 硬上限（防死循环）
        until_consensus = bool(discussion.get("until_consensus"))
        effective_total_rounds = total_rounds
        if until_consensus:
            effective_total_rounds = max(
                total_rounds, int(CONSENSUS_CONFIG["max_rounds"])
            )

        # 共识模式分两段：
        #   发散期（第 1~diverge_rounds 轮）→ 鼓励新角度、新论据，尽量发散；
        #   第 diverge_rounds 轮结束 → 评价裁判产出「局中总结」；
        #   收敛期（第 diverge_rounds+1 轮起）→ 围绕局中总结收敛观点。
        diverge_rounds = int(CONSENSUS_CONFIG.get("diverge_rounds", 3))
        converge_start = diverge_rounds + 1
        midterm_block: str = ""   # 局中总结注入块，发散期结束后填充

        # 推 stream_start
        await sse_manager.publish(stream_id, "stream_start", {
            "stream_id": stream_id,
            "type": "discussion",
            "discussion_id": discussion_id,
            "topic": topic,
            "total_rounds": total_rounds,
            "created_at": discussion["created_at"],
        })

        # 累计各轮总结（供共识判定使用，避免只看单轮导致误判）
        all_round_summaries: List[str] = []

        # 总时长守卫：max_duration_seconds 此前只入库、从未被检查，
        # 长讨论会一直占用全局单任务锁。超时后不再开启新一轮（不打断进行中的轮次），
        # 交由下方统一收尾流程落终态。
        max_duration_seconds = int(
            discussion.get("max_duration_seconds")
            or DISCUSSION_CONFIG["default_duration_seconds"]
        )
        run_start_ts = time.monotonic()

        # 逐轮
        for round_no in range(1, effective_total_rounds + 1):
            if round_no > 1 and (time.monotonic() - run_start_ts) >= max_duration_seconds:
                logger.warning(
                    "讨论已达最长时长 %ss，不再开启新一轮: discussion=%s round=%s",
                    max_duration_seconds, discussion_id, round_no,
                )
                break

            # 检查暂停（进入新一轮前）
            await _wait_if_paused(discussion_id)

            # 更新 current_round
            await asyncio.to_thread(
                discussion_repo.update_discussion_current_round_sync,
                discussion_id, round_no,
            )

            # 重新读取参与者（禁言状态可能变化）
            participants = await asyncio.to_thread(
                discussion_repo.get_discussants_sync, discussion_id,
            )
            active_ids = [p["ai_id"] for p in participants if not p["is_banned"]]
            banned_ids = [p["ai_id"] for p in participants if p["is_banned"]]

            # 每轮重新读取秩序裁判：弹劾换人后，下一轮起就由新裁判计分
            _order_row = await asyncio.to_thread(
                discussion_repo.get_judge_sync, discussion_id, "order"
            )
            order_judge_model = ((_order_row or {}).get("ai_id")
                                 or DISCUSSION_JUDGES["order"]["ai_id"])
            order_judge_name = ((_order_row or {}).get("display_name")
                                or order_judge_model)

            # 推 round_start
            await sse_manager.publish(stream_id, "round_start", {
                "round": round_no,
                "total_rounds": effective_total_rounds,
                "active_participants": active_ids,
                "banned_participants": banned_ids,
            })

            round_start_ts = time.monotonic()
            # 本轮在场席位名单（注入团队意识，让 AI 知道队友是谁）
            seat_names = ", ".join(
                (p.get("display_name") or p["ai_id"])
                for p in participants if not p["is_banned"]
            )
            order_in_round = 0
            round_messages: List[Dict[str, Any]] = []
            compliance_results: List[Dict[str, Any]] = []

            # 各 discussant 依次发言（跳过被禁言者）
            for p in participants:
                if p["is_banned"]:
                    continue

                # 检查暂停（每次发言前）
                await _wait_if_paused(discussion_id)

                order_in_round += 1
                recent = await asyncio.to_thread(
                    discussion_repo.get_recent_messages_sync,
                    discussion_id, DISCUSSION_CONFIG["context_window_messages"],
                )

                msg = await _run_one_speak(
                    discussion=discussion,
                    round_no=round_no,
                    order_in_round=order_in_round,
                    speaker=p,
                    recent_messages=recent,
                    stream_id=stream_id,
                    converge_phase=(until_consensus and round_no >= converge_start),
                    midterm_block=midterm_block,
                    teammates=seat_names,
                )
                if msg is None:
                    continue
                round_messages.append(msg)
                compliance_results.append({
                    "ai_id": msg["ai_id"],
                    "status": msg["compliance_status"],
                })

            # 计算当前累计分（供 order_judge 参考）
            # 键用席位名：同一模型占多个席位时，用 ai_id 会让同源席位互相覆盖
            current_scores = {
                p["display_name"]: round(float(p["total_score"] or 0.0), 1)
                for p in participants
            }

            # 秩序裁判评分（只对本轮实际发言者计分，缺席者标记 absent 不入榜）
            active_ids_this_round = [m["ai_id"] for m in round_messages]

            # 评分是串行长耗时环节（裁判限流时可达数分钟）。
            # 先推一条进度事件，避免前端长时间零输出被误判成"卡死"。
            await sse_manager.publish(stream_id, "judge_scoring", {
                "round": round_no,
                "total_rounds": effective_total_rounds,
                "judge": order_judge_model,
                "message": f"{order_judge_name}（{order_judge_model}）正在为本轮发言评分…",
            })

            judge_result = await score_round(
                discussion_id=discussion_id,
                topic=topic,
                round_no=round_no,
                total_rounds=effective_total_rounds,
                round_messages=round_messages,
                compliance_results=compliance_results,
                current_scores=current_scores,
                all_discussants=participants,
                active_ai_ids=active_ids_this_round,
                judge_ai=order_judge_model,
                # 发散期的轮次总结保持原样（核心进展+关键分歧）；
                # 收敛期才要求写「已达成/仍分歧」双字段，供共识判定使用
                until_consensus=(until_consensus and round_no >= converge_start),
            )

            # 落库 round_scores（仅发言者，缺席者不留 0 分记录）
            scored_list = [s for s in judge_result["scores"] if not s.get("absent")]
            await asyncio.to_thread(
                discussion_repo.upsert_round_scores_sync,
                discussion_id, round_no, scored_list, order_judge_model,
            )

            # 累加 participant.total_score（缺席者不累加）
            # 按席位 id 累加：同源席位各自计分，不互相叠加
            for s in scored_list:
                await asyncio.to_thread(
                    discussion_repo.increment_seat_score_sync,
                    discussion_id, s.get("seat_id"), s["weighted_total"],
                    ai_id=s.get("ai_id"),
                )

            # 关联每轮发言的 weighted_total 到 message
            # （简化：本轮每位 AI 只对应 round_score，不由 message 表回写）

            # 推 round_score
            await sse_manager.publish(stream_id, "round_score", {
                "round": round_no,
                "scores": judge_result["scores"],
                "scored_by": order_judge_model,
                "judge_type": "order",
            })

            # 刷新计分榜并推 scoreboard_update
            leaderboard = await asyncio.to_thread(
                discussion_repo.get_leaderboard_sync, discussion_id,
            )
            # 本轮分数映射（按席位：同源席位各自取自己的分）
            round_score_map = {
                (s.get("seat_id") or s["display_name"]): s["weighted_total"]
                for s in judge_result["scores"]
            }
            leaderboard_event = []
            for item in leaderboard:
                seat_key = item.get("id") or item["display_name"]
                leaderboard_event.append({
                    "rank": item["rank"],
                    "ai_id": item["ai_id"],
                    "display_name": item["display_name"],
                    "seat_id": item.get("id"),
                    "total_score": item["total_score"],
                    "round_score": round_score_map.get(seat_key, 0.0),
                })

            # 禁言判定
            participants_after = await asyncio.to_thread(
                discussion_repo.get_discussants_sync, discussion_id,
            )
            ban_actions = await _compute_ban_actions(
                discussion_id, round_no, participants_after,
            )

            # 执行禁言 + 推 ban_action
            banned_now: List[str] = []
            for ba in ban_actions:
                await _execute_ban_action(
                    stream_id, discussion_id, ba, round_no, order_judge_model,
                )
                banned_now.append(ba["ai_id"])

            # 解除到期禁言
            participants_refreshed = await asyncio.to_thread(
                discussion_repo.get_discussants_sync, discussion_id,
            )
            for p in participants_refreshed:
                if p["is_banned"] and p.get("ban_end_round") and p["ban_end_round"] <= round_no:
                    # 解禁必须按席位，和上面的禁言对称：
                    # 按 ai_id 解禁会清掉该模型全部席位的禁言，
                    # 同一模型占两席位、只禁其一时会误放行另一个还在禁言期的席位。
                    await asyncio.to_thread(
                        discussion_repo.set_seat_banned_sync,
                        discussion_id, p.get("id"), False, None, None,
                        ai_id=p["ai_id"],
                    )
                    await asyncio.to_thread(
                        discussion_repo.deactivate_ban_sync,
                        discussion_id, p["ai_id"], seat_id=p.get("id"),
                    )
                    await asyncio.to_thread(
                        discussion_repo.insert_ban_record_sync,
                        discussion_id, p["ai_id"], p["display_name"],
                        "unbanned", "禁言轮次结束，自动恢复",
                        None, round_no + 1, 1, False,
                        seat_id=p.get("id"),
                    )
                    await sse_manager.publish(stream_id, "ban_action", {
                        "ai_id": p["ai_id"],
                        "display_name": p["display_name"],
                        "seat_id": p.get("id"),
                        "action": "unbanned",
                        "reason": "禁言轮次结束，自动恢复",
                        "effective_from_round": round_no + 1,
                    })

            # 推 scoreboard_update（更新 banned 列表）
            # banned 用席位 id：同源席位被禁时不会连带标记成全部禁言
            banned_ids_after = [
                p.get("id") or p["ai_id"]
                for p in participants_refreshed if p["is_banned"]
            ]
            await sse_manager.publish(stream_id, "scoreboard_update", {
                "round": round_no,
                "leaderboard": leaderboard_event,
                "banned": banned_ids_after,
            })

            # 轮次总结落库 + 推 round_end
            round_duration_ms = int((time.monotonic() - round_start_ts) * 1000)
            all_round_summaries.append(judge_result["round_summary"])
            await asyncio.to_thread(
                discussion_repo.insert_round_summary_sync,
                discussion_id, round_no,
                len(round_messages), round_duration_ms,
                judge_result["round_summary"],
            )
            await asyncio.to_thread(
                discussion_repo.increment_rounds_completed_sync, discussion_id,
            )
            await sse_manager.publish(stream_id, "round_end", {
                "round": round_no,
                "total_rounds": effective_total_rounds,
                "messages_count": len(round_messages),
                "duration_ms": round_duration_ms,
                "round_summary": judge_result["round_summary"],
            })

            # ---- 发散期结束：评价裁判出「局中总结」，收敛期据此收敛 ----
            if (until_consensus and round_no == diverge_rounds
                    and round_no < effective_total_rounds and not midterm_block):
                midterm_block = await _run_midterm_summary(
                    discussion=discussion,
                    round_summaries=all_round_summaries,
                    diverge_rounds=diverge_rounds,
                    stream_id=stream_id,
                )

            # ---- 共识检测（「直到共识」模式）----
            # 规则：第 min_check_round 轮（含）起，每轮结束由裁判判定一次；
            #       达成 → 立即结束；未达成 → 继续下一轮，直到 effective_total_rounds 上限。
            # 注意：原先额外要求 round_no < total_rounds，导致最后一轮不判定，
            #       「直到共识」在 3 轮设定下完全失效。
            if until_consensus:
                # 发散期不做共识判定：此时本就在鼓励发散，判了必然 false，
                # 白白多一次裁判调用。从收敛期第 1 轮（diverge_rounds+1）起判定。
                min_round = max(
                    int(CONSENSUS_CONFIG["min_check_round"]), converge_start)
                if round_no >= min_round:
                    consensus = await _check_consensus(
                        discussion=discussion,
                        rounds_completed=round_no,
                        round_summaries=all_round_summaries,
                        stream_id=stream_id,
                        latest_messages=round_messages,
                    )
                    await sse_manager.publish(stream_id, "consensus_check", {
                        "round": round_no,
                        "reached": consensus["reached"],
                        "reason": consensus["reason"],
                        "judge": consensus["judge"],
                    })
                    if consensus["reached"]:
                        await asyncio.to_thread(
                            discussion_repo.set_consensus_reached_sync,
                            discussion_id,
                        )
                        end_reason = "consensus"
                        await sse_manager.publish(stream_id, "consensus_reached", {
                            "round": round_no,
                            "reason": consensus["reason"],
                            "judge": consensus["judge"],
                            "message": "讨论已达成共识，提前结束",
                        })
                        break

        # 全部轮次完成，进入收尾
        end_reason = "max_rounds" if end_reason != "consensus" else end_reason

    except _PauseTimeout:
        # 暂停超时 ≠ 用户手动终止：分开记，别让终局裁判和统计拿到错的结束原因
        end_reason = "pause_timeout"
        logger.warning("讨论 %s 因暂停超时终止", discussion_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("讨论流水线异常: id=%s", discussion_id)
        end_reason = "error"

    # ---- 收尾 ----
    try:
        total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)

        # 终态必须与落库 status 一致（done / terminated / failed）。
        # 不能把 end_reason（consensus / user_manual / error）直接当 status 推给前端，
        # 否则前端状态机会进入非法状态（错误提示不弹、控制按钮失效）。
        final_status = (
            "terminated" if end_reason in ("user_manual", "pause_timeout")
            else "failed" if end_reason == "error"
            else "done"
        )

        # 最终排名
        await asyncio.to_thread(discussion_repo.set_final_rank_sync, discussion_id)
        final_ranking = await asyncio.to_thread(
            discussion_repo.get_leaderboard_sync, discussion_id,
        )
        rounds_completed_row = await asyncio.to_thread(
            discussion_repo.get_discussion_sync, discussion_id,
        )
        rounds_completed = (rounds_completed_row or {}).get("rounds_completed", 0)

        # 更新 discussions 主表
        await asyncio.to_thread(
            discussion_repo.update_discussion_status_sync,
            discussion_id,
            final_status,
            end_reason=end_reason,
            completed=True,
        )
        await asyncio.to_thread(
            discussion_repo.update_discussion_total_duration_sync,
            discussion_id, total_duration_ms,
        )

        # 推 discussion_end
        final_ranking_event = []
        for item in final_ranking:
            total = item["total_score"]
            avg = round(total / max(1, rounds_completed), 1)
            final_ranking_event.append({
                "rank": item["rank"],
                "ai_id": item["ai_id"],
                "display_name": item["display_name"],
                "total_score": total,
                "avg_score": avg,
            })
        await sse_manager.publish(stream_id, "discussion_end", {
            "discussion_id": discussion_id,
            "status": final_status,
            "end_reason": end_reason,
            "total_rounds_completed": rounds_completed,
            "total_duration_ms": total_duration_ms,
            "final_ranking": final_ranking_event,
        })

        # 评价裁判（正常完成或共识达成时；异常/终止不调用，避免浪费）
        if end_reason in ("max_rounds", "consensus"):
            discussion_final = await asyncio.to_thread(
                discussion_repo.get_discussion_sync, discussion_id,
            )
            await _run_evaluation_judge(
                discussion=discussion_final or {},
                stream_id=stream_id,
            )

        await sse_manager.publish(stream_id, "done", {})

    except Exception:  # noqa: BLE001
        logger.exception("讨论收尾失败: id=%s", discussion_id)
        try:
            await sse_manager.publish(stream_id, "error", {
                "message": "讨论收尾失败",
                "code": "INTERNAL_ERROR",
            })
            await sse_manager.publish(stream_id, "done", {})
        except Exception:  # noqa: BLE001
            pass
