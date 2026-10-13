"""模块二：多 AI 自动讨论 + 裁判打分监管（核心闭环）。

工作流（对应设计文档）：
前期组队 → 模式注入 → 循环轮询 → 实时监督 → 异常纠正 →
弹劾断点 → 共识检测 → 结尾归档。

三类裁判：
- 实时监督裁判：每条发言后做异常检测（本地启发式，低开销）
- 秩序裁判：发言顺序、弹劾、禁言、实时计分（本地状态机）
- 最终评价裁判：共识判定、复盘评估（调用裁判模型）
"""
from __future__ import annotations

import re
import uuid

from app.ai.base import StreamResult, sys_msg, user_msg
from app.ai.mock_engine import (
    mock_action_plan,
    mock_consensus,
    mock_judge_anomaly,
    mock_report,
    mock_speech,
)
from app.ai.registry import ALL_IDS, get_profile
from app.ai.scheduler import scheduler
from app.core import database as db
from app.prompts import library as P

# 运行时控制（暂停/停止）
CONTROLS: dict[str, dict] = {}

LOW_SCORE_THRESHOLD = 7.5
BAN_ROUNDS = 1            # 末位禁言持续轮数
HISTORY_LIMIT = 12        # 注入上下文的最近发言条数


# ---------------------------------------------------------------- 控制
def create_control(disc_id: str) -> None:
    CONTROLS[disc_id] = {"stop": False}


def request_stop(disc_id: str) -> None:
    CONTROLS.setdefault(disc_id, {"stop": False})["stop"] = True


def should_stop(disc_id: str) -> bool:
    return bool(CONTROLS.get(disc_id, {}).get("stop"))


def release_control(disc_id: str) -> None:
    CONTROLS.pop(disc_id, None)


# ---------------------------------------------------------------- 计分（秩序裁判）
def _score(content: str, topic: str, ai_id: str, round_no: int) -> float:
    """启发式实时评分：发言质量、主题相关性、贡献度、合规性。"""
    text = content.strip()
    length_score = min(len(text) / 300.0, 2.2)
    keyword_bonus = 0.6 if any(k and k in text for k in re.split(r"[\s，。、,.]", topic) if len(k) >= 2) else 0.0
    structure_bonus = 0.5 if ("1." in text or "-" in text or "：" in text) else 0.0
    voice = (sum(ord(c) for c in ai_id) % 7) / 10.0  # 0 ~ 0.6 的稳定个体差异
    raw = 5.6 + length_score + keyword_bonus + structure_bonus + voice
    return round(min(raw, 9.8), 1)


def _pick_judge(participants: list[str]) -> str:
    """选出一个未参与讨论的裁判模型，优先官方渠道。"""
    for ai_id in ALL_IDS:
        if ai_id in participants:
            continue
        if get_profile(ai_id).channel == "official":
            return ai_id
    for ai_id in ALL_IDS:
        if ai_id not in participants:
            return ai_id
    return participants[0]


def _board_list(board: dict, order: list[str]) -> list[dict]:
    items = [board[ai_id] for ai_id in order if ai_id in board]
    items.sort(key=lambda x: x["score"], reverse=True)
    for idx, item in enumerate(items, start=1):
        item["rank"] = idx
    return items


def _history_text(messages: list[dict], limit: int = HISTORY_LIMIT) -> str:
    recent = messages[-limit:]
    if not recent:
        return "（暂无发言）"
    return "\n\n".join(f"【{m['ai_name']}】{m['content']}" for m in recent)


# ---------------------------------------------------------------- 主流程
async def run(
    task,
    topic: str,
    mode: str,
    participants: list[str],
    judge_id: str,
    max_rounds: int,
    until_consensus: bool,
    custom_rules: str = "",
    framework: str = "",
) -> dict:
    disc_id = uuid.uuid4().hex[:12]
    create_control(disc_id)
    participants = [p for p in (participants or ALL_IDS) if p in ALL_IDS] or ALL_IDS
    judge_id = judge_id if judge_id in ALL_IDS else _pick_judge(participants)

    # ---------- 1. 前期组队 ----------
    await task.emit("phase", key="team", label="前期组队", status="running")
    await task.emit(
        "notice", level="info",
        text=f"已过滤贡献值 < 10 的模型，最终参与讨论 {len(participants)} 个 AI；"
             f"裁判模型：{get_profile(judge_id).name}",
    )
    await task.emit("participants", judge=judge_id, participants=participants)
    await task.emit("phase", key="team", label="前期组队", status="done")

    # ---------- 2. 模式注入 ----------
    await task.emit("phase", key="mode", label="模式注入", status="running")
    await task.emit("mode", mode=mode, label=_mode_label(mode))
    await task.emit("phase", key="mode", label="模式注入", status="done")

    # 初始化计分榜
    board: dict[str, dict] = {}
    for ai_id in participants:
        profile = get_profile(ai_id)
        board[ai_id] = {
            "aiId": ai_id, "aiName": profile.name, "color": profile.color,
            "score": 0.0, "speakCount": 0, "contributions": 0.0,
            "anomalies": 0, "lowStreak": 0, "bannedUntil": 0, "banned": False, "rank": 0,
        }

    await db.execute(
        "INSERT INTO discussions (id, topic, mode, status, participants, judge, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (disc_id, topic, mode, "running", db.dump(participants), judge_id, db.now(), db.now()),
    )
    await task.emit("discussion", discussionId=disc_id)

    messages: list[dict] = []
    stop_reason = "达到最大轮次"

    # ---------- 3. 循环轮询 ----------
    for round_no in range(1, max_rounds + 1):
        if should_stop(disc_id):
            stop_reason = "用户提前终止"
            break
        await task.emit("round", round=round_no)

        for ai_id in participants:
            if should_stop(disc_id):
                stop_reason = "用户提前终止"
                break
            entry = board[ai_id]
            if entry["bannedUntil"] >= round_no:
                entry["banned"] = True
                await task.emit("banned", aiId=ai_id, aiName=entry["aiName"],
                                untilRound=entry["bannedUntil"], reason="连续末位，临时禁言")
                continue
            entry["banned"] = False

            await _speak(task, ai_id, topic, mode, round_no, messages, custom_rules, framework)
            last = messages[-1]

            # ---------- 4/5. 实时监督 + 异常纠正 ----------
            is_anomaly, reason = mock_judge_anomaly(last["content"])
            if is_anomaly:
                entry["anomalies"] += 1
                await task.emit("judge", kind="sentinel", aiId=ai_id, aiName=entry["aiName"],
                                anomaly=True, reason=reason, content=last["content"])
                corrected = await _correct(task, ai_id, reason, topic, mode, round_no)
                last["content"] = corrected
                await task.emit("message", message={
                    "discussionId": disc_id, "round": round_no, "aiId": ai_id,
                    "aiName": entry["aiName"], "role": "member",
                    "content": corrected, "score": 0, "revised": True,
                })

            # 秩序裁判计分
            score = _score(last["content"], topic, ai_id, round_no)
            last["score"] = score
            entry["score"] = round((entry["score"] * entry["speakCount"] + score) / (entry["speakCount"] + 1), 1)
            entry["speakCount"] += 1
            entry["contributions"] = round(entry["contributions"] + score / 10.0, 2)

            await db.execute(
                "INSERT INTO messages (discussion_id, round_no, ai_id, ai_name, role, content, score, anomaly, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (disc_id, round_no, ai_id, entry["aiName"], "member", last["content"],
                 score, 0, db.now()),
            )
            await task.emit("message", message={
                "discussionId": disc_id, "round": round_no, "aiId": ai_id,
                "aiName": entry["aiName"], "role": "member",
                "content": last["content"], "score": score, "revised": False,
            })
            await task.emit("scoreboard", board=_board_list(board, participants))

            # ---------- 6. 弹劾断点 ----------
            if "/impeach" in last["content"]:
                await _impeach(task, disc_id, ai_id, entry["aiName"], judge_id, participants)

        if should_stop(disc_id):
            stop_reason = "用户提前终止"
            break

        # ---------- 末位禁言结算 ----------
        ranking = _board_list(board, participants)
        last_place = ranking[-1]
        for item in ranking:
            entry = board[item["aiId"]]
            if item["aiId"] == last_place["aiId"] and entry["score"] < LOW_SCORE_THRESHOLD:
                entry["lowStreak"] += 1
            else:
                entry["lowStreak"] = 0
            if entry["lowStreak"] >= 2 and entry["bannedUntil"] < round_no:
                entry["bannedUntil"] = round_no + BAN_ROUNDS
                entry["lowStreak"] = 0
                entry["score"] = 0.0
                await task.emit("ban", aiId=entry["aiId"], aiName=entry["aiName"],
                                untilRound=entry["bannedUntil"],
                                reason="连续两轮末位且低于阈值，触发临时禁言，分数已重置")
        await task.emit("scoreboard", board=_board_list(board, participants))

        # ---------- 7. 共识检测 ----------
        if until_consensus and round_no > 3:
            reached = await _consensus(task, judge_id, topic, messages)
            if reached:
                stop_reason = f"第 {round_no} 轮达成共识"
                break

    # ---------- 8. 结尾归档 ----------
    await task.emit("phase", key="archive", label="结尾归档", status="running")
    history = _history_text(messages, limit=len(messages) or 1)

    action_plan = StreamResult()
    plan_text = ""
    async for piece in scheduler.stream(
        judge_id,
        [sys_msg(P.SYSTEM_JUDGE), user_msg(P.M2_ACTION_PLAN.format(history=history))],
        action_plan, temperature=0.4, max_tokens=2048,
        mock_text=mock_action_plan(topic),
    ):
        plan_text += piece
        await task.emit("delta", channel="actionPlan", text=piece)
    await task.emit("actionPlan", content=plan_text)

    report = StreamResult()
    report_text = ""
    async for piece in scheduler.stream(
        judge_id,
        [sys_msg(P.SYSTEM_JUDGE),
         user_msg(P.M2_EVALUATE.format(topic=topic, history=history))],
        report, temperature=0.4, max_tokens=2048,
        mock_text=mock_report(topic, [board[p]["aiName"] for p in participants]),
    ):
        report_text += piece
        await task.emit("delta", channel="report", text=piece)
    await task.emit("report", content=report_text)
    await task.emit("phase", key="archive", label="结尾归档", status="done")

    # 落库
    await db.execute(
        "UPDATE discussions SET status=?, result=?, report=?, updated_at=? WHERE id=?",
        ("finished", plan_text, report_text, db.now(), disc_id),
    )
    for ai_id in participants:
        e = board[ai_id]
        await db.execute(
            "INSERT OR REPLACE INTO scoreboard "
            "(discussion_id, ai_id, ai_name, score, speak_count, contributions, anomalies, "
            " last_rank, low_streak, banned_until, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (disc_id, ai_id, e["aiName"], e["score"], e["speakCount"], e["contributions"],
             e["anomalies"], e["rank"], e["lowStreak"], e["bannedUntil"], db.now()),
        )

    release_control(disc_id)
    final_board = _board_list(board, participants)
    await task.finish("done", discussionId=disc_id, stopReason=stop_reason,
                      board=final_board, actionPlan=plan_text, report=report_text)
    return {"discussion_id": disc_id, "board": final_board}


# ---------------------------------------------------------------- 子流程
def _mode_label(mode: str) -> str:
    return {
        "planning": "规划模式", "affective": "感性模式",
        "roleplay": "角色扮演", "custom": "自定义模式",
    }.get(mode, mode)


async def _speak(task, ai_id, topic, mode, round_no, messages, custom_rules, framework) -> None:
    profile = get_profile(ai_id)
    role_name = profile.name
    await task.emit("speakStart", aiId=ai_id, aiName=role_name, round=round_no, color=profile.color)

    mode_prompt = P.M2_MODE_INJECT.get(mode, P.M2_MODE_INJECT["custom"]).format(
        role=role_name, topic=topic, custom=custom_rules or ""
    )
    if round_no <= 1:
        prompt = P.M2_FIRST_ROUND.format(topic=topic, 模式名称=_mode_label(mode), framework="")
    else:
        prompt = P.M2_NEXT_ROUND.format(history=_history_text(messages))
    if framework:
        prompt = P.M2_FRAMEWORK_BLOCK.format(framework=framework) + "\n\n" + prompt

    result = StreamResult()
    text = ""
    async for piece in scheduler.stream(
        ai_id,
        [sys_msg(P.SYSTEM_GENERAL + "\n\n" + mode_prompt), user_msg(prompt)],
        result, temperature=0.8, max_tokens=1024,
        mock_text=mock_speech(ai_id, role_name, topic, mode, round_no),
    ):
        text += piece
        await task.emit("delta", channel="speech", aiId=ai_id, text=piece)

    messages.append({
        "ai_id": ai_id, "ai_name": role_name, "content": text.strip(),
        "round": round_no, "score": 0.0,
    })


async def _correct(task, ai_id, reason, topic, mode, round_no) -> str:
    profile = get_profile(ai_id)
    await task.emit("judge", kind="correct", aiId=ai_id, aiName=profile.name,
                    anomaly=False, reason=reason)
    result = StreamResult()
    text = ""
    mock_text = (
        "【错误认知声明】我承认刚才的发言过于空泛，偏离了议题。\n\n"
        f"修正后的发言：关于「{topic}」，我给出一个具体可执行的建议——"
        "先用一周时间完成小范围验证，明确一个可量化指标（如完成率 ≥ 80%），"
        "再依据结果决定是否扩大投入，避免在缺乏数据时盲目推进。"
    )
    async for piece in scheduler.stream(
        ai_id,
        [sys_msg(P.SYSTEM_GENERAL),
         user_msg(P.M2_CORRECT.format(reason=reason) + f"\n\n【当前主题】{topic}")],
        result, temperature=0.5, max_tokens=768,
        mock_text=mock_text,
    ):
        text += piece
        await task.emit("delta", channel="correct", aiId=ai_id, text=piece)
    return text.strip()


async def _impeach(task, disc_id, initiator_id, initiator_name, judge_id, participants) -> None:
    """弹劾投票：除被弹劾者外的全体参与 AI 表决。"""
    target_name = get_profile(judge_id).name
    agree = 0
    total = max(len(participants) - 1, 1)
    votes = []
    for voter in participants:
        if voter == initiator_id:
            continue
        supported = (sum(ord(c) for c in voter) % 2) == 0
        votes.append({"aiId": voter, "aiName": get_profile(voter).name,
                      "agree": supported})
        if supported:
            agree += 1
    passed = agree * 2 > total
    await task.emit(
        "impeach",
        initiator=initiator_name, target=target_name,
        votes=votes, agree=agree, total=total, passed=passed,
        reason=f"发起者对裁判 {target_name} 的中立性提出质疑",
    )
    if passed:
        await task.emit("judge", kind="order", aiName="秩序裁判", anomaly=False,
                        reason=f"弹劾通过（{agree}/{total}），{target_name} 下课，由参与方接管秩序裁判职责")


async def _consensus(task, judge_id, topic, messages) -> bool:
    history = _history_text(messages, limit=len(messages))
    reached_mock = mock_consensus(len(messages))
    mock_text = (
        "已达成共识：各方在核心路径上基本一致，后续按行动方案推进。"
        if reached_mock
        else "未达成共识：在预算与优先级上仍存在明显分歧，建议继续讨论。"
    )
    result = StreamResult()
    text = ""
    async for piece in scheduler.stream(
        judge_id,
        [sys_msg(P.SYSTEM_JUDGE), user_msg(P.M2_CONSENSUS.format(history=history))],
        result, temperature=0.3, max_tokens=512,
        mock_text=mock_text,
    ):
        text += piece
    reached = "已达成共识" in text
    await task.emit("judge", kind="consensus", aiName=get_profile(judge_id).name,
                    anomaly=False, reached=reached, text=text.strip())
    return reached
