# ============================================================
# 文件：backend/app/services/extension_service.py
# 说明：扩展功能服务层（依据：多ai讨论板块prompt与工作流程.txt）
#   模块A：即时人机对话（快/串/并行 + 实时监督员 + 危险拦截）
#   模块C：智能组队（单AI/多AI推荐阵容）
#   模块E：认知蒸馏（13部分总结 / 裁判总结 / 框架演化）
#   模块B/F：弹劾投票 / 用户信用治理（圆桌会议）
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from app.config import AI_MODELS
from app.config.settings import ModelConfig
from app.config.business import DISCUSSION_JUDGES, TEAMUP_CONFIG
from app.config.prompts import (
    CHAT_ANOMALY_REWRITE,
    CHAT_DANGER_KEYWORDS,
    CHAT_PARALLEL_MODE,
    CHAT_QUICK_MODE,
    CHAT_SERIAL_MODE,
    CHAT_SUPERVISOR_CHECK,
    TEAM_AWARENESS,
    TEAM_ROLE_CHAT_PARALLEL,
    TEAM_ROLE_CHAT_QUICK,
    TEAM_ROLE_CHAT_SERIAL,
    DISTILL_FRAME_EVOLVE,
    DISTILL_INDIVIDUAL_PROMPT,
    DISTILL_JUDGE_PROMPT,
    DISC_IMPEACH_VOTE,
    GOV_ROUNDTABLE_PROMPT,
    TEAMUP_MULTI_PROMPT,
    TEAMUP_SINGLE_PROMPT,
    build_prompt,
)
from app.database import discussion_repo
from app.database import extension_repo
from app.services import memory_service
from app.services.ai_health import probe_all_models
from app.services.ai_scheduler import call_ai
from app.services.sse_manager import sse_manager

logger = logging.getLogger("app.extension_service")


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """从 AI 输出中尽量提取 JSON 对象。"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _extract_json_array(text: str) -> Optional[List[str]]:
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        val = json.loads(m.group(0))
        return val if isinstance(val, list) else None
    except json.JSONDecodeError:
        return None


# ============================================================
# 模块A：即时人机对话
# ============================================================

def check_danger(text: str) -> Optional[str]:
    """前置危险请求拦截。返回命中的关键词或 None。"""
    for kw in CHAT_DANGER_KEYWORDS:
        if kw in text:
            return kw
    return None


async def chat_supervisor_check(question: str, answer: str) -> Dict[str, Any]:
    """后置哨兵：判断 AI 回答是否异常。"""
    judge_ai = DISCUSSION_JUDGES["compliance"]["ai_id"]
    prompt = build_prompt(
        CHAT_SUPERVISOR_CHECK, question=question[:500], answer=answer[:800])
    result = await call_ai(
        judge_ai,
        [{"role": "user", "content": prompt}],
        task_type="judge",
        max_tokens=128,
        temperature=0.1,
    )
    if result.status != "success":
        return {"is_anomaly": False, "reason": ""}  # 哨兵失败不阻塞
    data = _extract_json_obj(result.content or "")
    if not data:
        return {"is_anomaly": False, "reason": ""}
    return {
        "is_anomaly": bool(data.get("is_anomaly")),
        "reason": str(data.get("reason", "")),
    }


async def chat_answer(
    *,
    session_id: int,
    question: str,
    stream_id: str,
) -> None:
    """
    即时对话主流程（SSE 推送）：
      1. 危险关键词拦截
      2. 读取会话与历史
      3. 按 quick/serial/parallel 三模式调用 AI
      4. 后置哨兵检查 → 异常则重写
      5. 若 memory_enabled 且存在认知框架，注入框架
    """
    session = await asyncio.to_thread(
        extension_repo.get_chat_session_sync, session_id)
    if session is None:
        await sse_manager.publish(stream_id, "error", {
            "message": "会话不存在", "code": "NOT_FOUND"})
        await sse_manager.publish(stream_id, "done", {})
        return

    # 1. 危险拦截
    danger_kw = check_danger(question)
    if danger_kw:
        await sse_manager.publish(stream_id, "danger_blocked", {
            "keyword": danger_kw,
            "message": "您的请求涉及危险内容，已被系统拦截并记录。",
        })
        await asyncio.to_thread(
            extension_repo.log_user_action_sync, "default",
            "danger_request", f"命中危险词：{danger_kw}", credit_delta=-5.0)
        await sse_manager.publish(stream_id, "done", {})
        return

    # 2. 用户消息落库 + 推送
    await asyncio.to_thread(
        extension_repo.insert_chat_message_sync, session_id, "user", question)
    await sse_manager.publish(stream_id, "chat_user", {"content": question})

    mode = session["mode"]
    history_rows = await asyncio.to_thread(
        extension_repo.list_chat_messages_sync, session_id, 50)
    ai_answers = [r for r in history_rows if r["role"] == "ai"]

    # 3. 认知框架注入
    frame_block = ""
    if session["memory_enabled"]:
        frame = await asyncio.to_thread(extension_repo.get_active_frame_sync)
        if frame:
            frame_block = f"\n\n【认知框架记忆】\n{frame['frame_text'][:1500]}"

    # 3.5 单模型直连：会话指定了模型时，只把「历史 + 本轮问题」原样发给它，
    #     不套用快速/串行/并行的模式提示词，也不做监督员重写与框架注入。
    solo_ai = (session.get("ai_id") or "").strip()
    if solo_ai and solo_ai in AI_MODELS:
        history = [
            {"role": "assistant" if r["role"] == "ai" else "user",
             "content": r["content"]}
            for r in history_rows[-20:]
            if r["role"] in ("user", "ai") and (r["content"] or "").strip()
        ]
        result = await call_ai(
            solo_ai,
            history,
            task_type="discussion",
            task_id=session_id,
            max_tokens=2048,
            temperature=0.7,
        )
        if result.status != "success":
            await sse_manager.publish(stream_id, "chat_ai_error", {
                "ai_id": solo_ai, "message": result.error_message or "调用失败"})
            await sse_manager.publish(stream_id, "chat_done", {"answered": []})
            await sse_manager.publish(stream_id, "done", {})
            return

        content = (result.content or "").strip()
        msg_id = await asyncio.to_thread(
            extension_repo.insert_chat_message_sync,
            session_id, "ai", content, ai_id=solo_ai)
        await sse_manager.publish(stream_id, "chat_ai", {
            "message_id": msg_id,
            "ai_id": solo_ai,
            "display_name": _display_name(solo_ai),
            "content": content,
        })
        await sse_manager.publish(stream_id, "chat_done", {
            "answered": [solo_ai]})
        await sse_manager.publish(stream_id, "done", {})
        return

    # 选定回答组（quick=1个；serial/parallel=3个）
    if mode == "quick":
        answerers = [DISCUSSION_JUDGES["compliance"]["ai_id"]]
    else:
        answerers = [p.ai_id for p in _pick_chat_models()[:3]]

    # 4. 逐个调用
    results: List[Dict[str, Any]] = []
    for i, ai_id in enumerate(answerers):
        # 团队意识：让每个回答席位知道自己是协作链上的一环（不是唯一权威）。
        # 注意：会话指定了单模型时（solo 直连）走前面分支，不注入任何附加提示词。
        if mode == "quick":
            prompt = CHAT_QUICK_MODE + frame_block
            team_block = build_prompt(
                TEAM_AWARENESS,
                role_line=TEAM_ROLE_CHAT_QUICK,
                peers_line="你的回答还会交给「监督员」席位做异常复核。",
            )
        elif mode == "serial" and i > 0:
            prev = "\n\n".join(
                f"[{r['ai_id']}]: {r['content'][:400]}" for r in results)
            prompt = build_prompt(CHAT_SERIAL_MODE, history=prev) + frame_block
            team_block = build_prompt(
                TEAM_AWARENESS,
                role_line=build_prompt(
                    TEAM_ROLE_CHAT_SERIAL, index=i + 1, total=len(answerers)),
                peers_line=f"本链条的席位顺序：{' → '.join(answerers)}（你是第 {i + 1} 环）。",
            )
        else:  # parallel 或 serial 首个
            teammates = ", ".join(a for a in answerers if a != ai_id)
            prompt = build_prompt(CHAT_PARALLEL_MODE, teammates=teammates) + frame_block
            team_block = build_prompt(
                TEAM_AWARENESS,
                role_line=build_prompt(
                    TEAM_ROLE_CHAT_PARALLEL, index=i + 1, total=len(answerers)),
                peers_line=f"与你同时独立作答的席位：{teammates}。",
            )
        prompt = prompt + "\n\n" + team_block

        result = await call_ai(
            ai_id,
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": question},
            ],
            task_type="discussion",
            task_id=session_id,
            max_tokens=1024,
            temperature=0.7,
        )
        if result.status != "success":
            await sse_manager.publish(stream_id, "chat_ai_error", {
                "ai_id": ai_id, "message": result.error_message or "调用失败"})
            continue

        content = (result.content or "").strip()
        msg_id = await asyncio.to_thread(
            extension_repo.insert_chat_message_sync,
            session_id, "ai", content, ai_id=ai_id)

        # 5. 后置哨兵（可选）
        anomaly_info: Dict[str, Any] = {"is_anomaly": False, "reason": ""}
        if session["supervisor_enabled"]:
            anomaly_info = await chat_supervisor_check(question, content)
            if anomaly_info["is_anomaly"]:
                await asyncio.to_thread(
                    extension_repo.mark_chat_message_anomaly_sync,
                    msg_id, anomaly_info["reason"])
                await sse_manager.publish(stream_id, "chat_anomaly", {
                    "ai_id": ai_id, "reason": anomaly_info["reason"]})
                # 错误认知声明 + 重写
                rewrite_prompt = build_prompt(
                    CHAT_ANOMALY_REWRITE, reason=anomaly_info["reason"])
                rr = await call_ai(
                    ai_id,
                    [
                        {"role": "system", "content": rewrite_prompt + frame_block},
                        {"role": "user", "content": question},
                    ],
                    task_type="discussion",
                    task_id=session_id,
                    max_tokens=1024,
                    temperature=0.5,
                )
                if rr.status == "success":
                    new_content = (rr.content or "").strip()
                    await asyncio.to_thread(
                        extension_repo.insert_chat_message_sync,
                        session_id, "ai", new_content, ai_id=ai_id,
                        rewrite_of=msg_id)
                    content = new_content
                    await sse_manager.publish(stream_id, "chat_rewrite", {
                        "ai_id": ai_id, "content": new_content})

        results.append({"ai_id": ai_id, "content": content})
        await sse_manager.publish(stream_id, "chat_ai", {
            "message_id": msg_id,
            "ai_id": ai_id,
            "display_name": _display_name(ai_id),
            "content": content,
        })

    await sse_manager.publish(stream_id, "chat_done", {
        "answered": [r["ai_id"] for r in results],
    })
    await sse_manager.publish(stream_id, "done", {})


def _pick_chat_models() -> List["ModelConfig"]:
    """即时对话默认挑选的 3 个模型（不同渠道优先）。"""
    preferred = ["claude", "deepseek", "kimi"]
    picked = [AI_MODELS[k] for k in preferred if k in AI_MODELS]
    if len(picked) < 3:
        picked = list(AI_MODELS.values())[:3]
    return picked


def _display_name(ai_id: str) -> str:
    cfg = AI_MODELS.get(ai_id)
    return cfg.display_name if cfg else ai_id


# ============================================================
# 模块C：智能组队
# ============================================================

def _available_model_ids() -> List[str]:
    """已配置密钥、可参与讨论的模型 id 列表（排除无 key 的离线模型）。"""
    from app.config.settings import (
        DEEPSEEK, KIMI, RELAY, WENXIN, ZHIPU,
    )

    key_map = {
        "deepseek": DEEPSEEK.api_key,
        "zhipu": ZHIPU.api_key,
        "wenxin": WENXIN.api_key,
        "kimi": KIMI.api_key,
    }
    out: List[str] = []
    for ai_id, cfg in AI_MODELS.items():
        ok = bool(RELAY.api_key) if cfg.channel == "relay" else bool(key_map.get(ai_id))
        if ok:
            out.append(ai_id)
    return out


async def _filter_online(ids: List[str]) -> List[str]:
    """
    过滤出当前在线的模型 id。

    _available_model_ids 只判断「是否配置了密钥」，中转站额度耗尽时
    仍会认为 relay 模型可用；若把它们放进候选，AI 会推荐无法出场的席位，
    用户按推荐开会等于空转。探活失败时原样返回，不阻断流程。
    """
    try:
        status = await probe_all_models()
        online = {
            m.get("ai_id")
            for m in (status.get("models") or [])
            if m.get("status") in ("online", "degraded")
        }
    except Exception:
        return ids
    if not online:
        return ids
    return [a for a in ids if a in online] or ids


def _model_list_text(ids: List[str]) -> str:
    """把候选模型渲染为 'id(名称), id(名称)' 形式，便于模型准确输出 id。"""
    return ", ".join(
        f"{ai_id}({AI_MODELS[ai_id].display_name})"
        for ai_id in ids if ai_id in AI_MODELS
    )


def _normalize_teamup_payload(
    data: Dict[str, Any], enabled: List[str],
) -> Dict[str, Any]:
    """校验并补齐组队结果：3 位参赛 + 3 位裁判 + 模式 + 严格度。"""
    if not enabled:
        enabled = list(AI_MODELS.keys())

    # ---- 参赛者：取前 3 个有效 id，不足则补齐 ----
    participants: List[str] = []
    for pid in (data.get("participants") or [])[:TEAMUP_CONFIG["participant_count"]]:
        if pid in AI_MODELS and pid not in participants:
            participants.append(pid)
    for pid in enabled:
        if len(participants) >= TEAMUP_CONFIG["participant_count"]:
            break
        if pid not in participants:
            participants.append(pid)
    participants = participants[:TEAMUP_CONFIG["participant_count"]]

    # ---- 裁判：三个裁判位各自取一个，尽量互不相同 ----
    raw_judges = data.get("judges") or {}
    single_judge = data.get("judge")
    judges: Dict[str, str] = {}
    used: List[str] = []
    for slot in TEAMUP_CONFIG["judge_slots"]:
        jid = raw_judges.get(slot) or single_judge
        if jid not in AI_MODELS:
            jid = ""
        if not jid or jid in used:
            # 从候选里挑一个尚未占用的（优先非参赛者）
            pool = [x for x in enabled if x not in used]
            preferred = [x for x in pool if x not in participants] or pool
            jid = preferred[0] if preferred else ""
        if jid:
            judges[slot] = jid
            used.append(jid)

    mode = str(data.get("mode") or "").strip()
    if mode not in ("free", "planning", "affective"):
        mode = "free"

    strictness = str(data.get("strictness") or "").strip().lower()
    if strictness not in ("loose", "medium", "strict"):
        strictness = "medium"

    return {
        "participants": participants,
        "judges": judges,
        "mode": mode,
        "strictness": strictness,
    }


def _tally_votes(votes: List[Dict[str, Any]], enabled: List[str]) -> Dict[str, Any]:
    """按文档规则统计票数：participants 取前 3 高频；judge/mode/strictness 取最高频。"""
    p_count: "Counter" = Counter()
    j_count: Dict[str, "Counter"] = {s: Counter() for s in TEAMUP_CONFIG["judge_slots"]}
    m_count: "Counter" = Counter()
    s_count: "Counter" = Counter()

    for v in votes:
        for pid in (v.get("participants") or []):
            if pid in AI_MODELS:
                p_count[pid] += 1
        raw_judges = v.get("judges") or {}
        single_judge = v.get("judge")
        for slot in TEAMUP_CONFIG["judge_slots"]:
            jid = raw_judges.get(slot) or single_judge
            if jid in AI_MODELS:
                j_count[slot][jid] += 1
        if v.get("mode"):
            m_count[str(v["mode"]).strip()] += 1
        if v.get("strictness"):
            s_count[str(v["strictness"]).strip().lower()] += 1

    top_participants = [pid for pid, _ in p_count.most_common()]
    judges = {slot: (j_count[slot].most_common(1)[0][0] if j_count[slot] else "")
              for slot in TEAMUP_CONFIG["judge_slots"]}

    return _normalize_teamup_payload(
        {
            "participants": top_participants,
            "judges": judges,
            "mode": m_count.most_common(1)[0][0] if m_count else "free",
            "strictness": s_count.most_common(1)[0][0] if s_count else "medium",
        },
        enabled,
    )


async def teamup_recommend(topic: str, *, multi: bool = False) -> Dict[str, Any]:
    """
    智能组队：推荐 3 位参赛 AI + 3 位裁判 + 讨论模式 + 共识严格度。
    - multi=False：单模型快速推荐
    - multi=True ：多个 AI 各自提案 → 按票数统计（participants/judges/mode/strictness 各取最高频）
    返回含 votes / reasons，供前端展示投票明细与各模型理由。
    """
    enabled = _available_model_ids() or list(AI_MODELS.keys())
    # 只把当前在线的模型交给 AI 挑选，避免推荐出无法出场的席位
    enabled = await _filter_online(enabled)
    model_list = _model_list_text(enabled)
    coordinator = DISCUSSION_JUDGES["evaluation"]["ai_id"]

    # ---------- 策略一：单模型推荐 ----------
    if not multi:
        prompt = build_prompt(TEAMUP_SINGLE_PROMPT, topic=topic, model_list=model_list)
        result = await call_ai(
            coordinator, [{"role": "user", "content": prompt}],
            task_type="system_check", max_tokens=512, temperature=0.3,
            # 推荐人以 evaluation 裁判为主；中转额度耗尽时需由官方模型接手
            fallback=DISCUSSION_JUDGES["evaluation"].get("fallback") or None)
        if result.status == "success":
            data = _extract_json_obj(result.content or "")
            if data:
                normalized = _normalize_teamup_payload(data, enabled)
                normalized["source"] = "single"
                normalized["reason"] = str(data.get("reason") or "").strip()
                return normalized
            # 兼容旧格式：只返回了数组
            ids = _extract_json_array(result.content or "")
            if ids:
                normalized = _normalize_teamup_payload({"participants": ids}, enabled)
                normalized["source"] = "single"
                normalized["reason"] = ""
                return normalized

    # ---------- 策略二：多 AI 讨论决定（投票统计） ----------
    else:
        council_ids = [m.ai_id for m in _pick_chat_models()][:TEAMUP_CONFIG["council_size"]]
        if not council_ids:
            council_ids = enabled[:TEAMUP_CONFIG["council_size"]]

        votes: List[Dict[str, Any]] = []
        reasons: List[Dict[str, str]] = []

        async def _one_vote(ai_id: str) -> None:
            p = build_prompt(TEAMUP_MULTI_PROMPT, topic=topic, model_list=model_list)
            r = await call_ai(
                ai_id, [{"role": "user", "content": p}],
                task_type="system_check", max_tokens=512, temperature=0.5)
            if r.status != "success" or not r.content:
                return
            data = _extract_json_obj(r.content)
            if not data:
                return
            votes.append(data)
            reasons.append({
                "ai_id": ai_id,
                "display_name": AI_MODELS[ai_id].display_name if ai_id in AI_MODELS else ai_id,
                "reason": str(data.get("reason") or "").strip()[:300],
            })

        await asyncio.gather(*[_one_vote(ai_id) for ai_id in council_ids])

        if votes:
            tally = _tally_votes(votes, enabled)
            tally["source"] = "multi"
            tally["reasons"] = reasons
            tally["votes"] = {
                "participants": dict(Counter(
                    pid for v in votes for pid in (v.get("participants") or [])
                    if pid in AI_MODELS
                )),
                "mode": dict(Counter(
                    str(v.get("mode")) for v in votes if v.get("mode")
                )),
                "strictness": dict(Counter(
                    str(v.get("strictness")).lower() for v in votes if v.get("strictness")
                )),
            }
            return tally

    # ---------- 兜底 ----------
    fallback = _normalize_teamup_payload({}, enabled)
    fallback["source"] = "fallback"
    fallback["reason"] = "AI 推荐不可用，已按可用模型自动组队"
    return fallback


# ============================================================
# 模块B/F：弹劾投票
# ============================================================

# 裁判角色中文名（与 discussion_repo._JUDGE_META 保持一致；此处只用于事件文案）
JUDGE_ROLE_LABELS = {
    "compliance": "实时监督裁判",
    "order": "秩序裁判",
    "evaluation": "最终评价裁判",
}


async def _pick_replacement_judge(discussion_id: int,
                                  excluded_ai_id: str) -> Optional[str]:
    """
    挑选替补裁判。

    三条硬约束：
      1. 必须已注册（AI_MODELS）；
      2. 必须探活可用（online / degraded）——否则可能换上一个 403 的死号，
        弹劾通过反而让下一轮没人判；
      3. 必须不在本场在场名单里——participants 有
         UNIQUE(discussion_id, ai_id, role)，撞上会更新失败，
         也避免让讨论者转去当裁判。
    """
    try:
        health = await probe_all_models()
        ranked = {m["ai_id"]: m.get("status")
                  for m in health.get("models", [])}
    except Exception:  # noqa: BLE001
        logger.warning("探活失败，替补候选退化为全部已注册模型")
        ranked = {k: "unknown" for k in AI_MODELS}

    rows = await asyncio.to_thread(
        discussion_repo.get_participants_sync, discussion_id)
    onstage = {r["ai_id"] for r in rows} | {excluded_ai_id}

    def _rank(ai_id: str) -> int:
        # online 优先，degraded 次之，状态未知最后
        return {"online": 0, "degraded": 1}.get(ranked.get(ai_id, ""), 2)

    candidates = [
        k for k in AI_MODELS
        if k not in onstage and ranked.get(k) in ("online", "degraded", "unknown")
    ]
    if not candidates:
        return None
    candidates.sort(key=_rank)
    return candidates[0]


async def run_impeachment(
    *,
    discussion_id: int,
    initiator_ai_id: str,
    target_role: str,
    reason: str,
    stream_id: str,
) -> Dict[str, Any]:
    """
    弹劾流程：
      1. 记录弹劾
      2. 所有未禁言讨论者投票（/agree 或 /disagree）
      3. 过半数同意 → 通过；否则驳回
      4. 通过时从非裁判模型中选出替补
    """
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id)
    if discussion is None:
        return {"error": "讨论不存在"}

    # 找到被弹劾裁判：以本场 participants 为准（可能已被前一次弹劾换过），
    # 取不到再回落默认配置——否则二次弹劾弹掉的是已经下台的旧裁判
    if target_role not in DISCUSSION_JUDGES:
        return {"error": f"无效的裁判角色: {target_role}"}
    judge_row = await asyncio.to_thread(
        discussion_repo.get_judge_sync, discussion_id, target_role)
    target_ai_id = ((judge_row or {}).get("ai_id")
                    or DISCUSSION_JUDGES[target_role]["ai_id"])

    participants = await asyncio.to_thread(
        discussion_repo.get_discussants_sync, discussion_id)
    discussants = [p for p in participants if not p["is_banned"]]

    recent = await asyncio.to_thread(
        discussion_repo.get_recent_messages_sync, discussion_id, 10)
    recent_lines = "\n".join(
        f"[{m.get('display_name', m.get('ai_id'))}]: {m.get('content', '')[:200]}"
        for m in recent) or "（暂无）"

    round_no = discussion.get("current_round") or 1
    imp_id = await asyncio.to_thread(
        extension_repo.insert_impeachment_sync,
        discussion_id, round_no, initiator_ai_id, target_role,
        target_ai_id, reason)

    await sse_manager.publish(stream_id, "impeachment_start", {
        "impeachment_id": imp_id,
        "initiator": initiator_ai_id,
        "target_role": target_role,
        "target_ai_id": target_ai_id,
        "reason": reason,
    })

    # 投票
    votes: Dict[str, str] = {}
    agree = disagree = 0
    for p in discussants:
        if p["ai_id"] == initiator_ai_id:
            votes[p["ai_id"]] = "agree"  # 发起者默认同意
            agree += 1
            continue
        prompt = build_prompt(
            DISC_IMPEACH_VOTE,
            initiator=initiator_ai_id,
            reason=reason,
            target_judge=target_ai_id,
            target_role=target_role,
            recent_messages=recent_lines,
        )
        r = await call_ai(
            p["ai_id"], [{"role": "user", "content": prompt}],
            task_type="judge", task_id=discussion_id,
            max_tokens=128, temperature=0.3)
        if r.status != "success":
            votes[p["ai_id"]] = "abstain"
            continue
        text = (r.content or "").lower()
        if "/agree" in text or "同意" in text:
            votes[p["ai_id"]] = "agree"
            agree += 1
        elif "/disagree" in text or "反对" in text:
            votes[p["ai_id"]] = "disagree"
            disagree += 1
        else:
            votes[p["ai_id"]] = "abstain"

        await sse_manager.publish(stream_id, "impeachment_vote", {
            "impeachment_id": imp_id,
            "ai_id": p["ai_id"],
            "vote": votes[p["ai_id"]],
        })

    total_voters = max(1, len(discussants))
    passed = agree > total_voters / 2

    replacement_ai_id = None
    if passed:
        replacement_ai_id = await _pick_replacement_judge(
            discussion_id, target_ai_id)

    await asyncio.to_thread(
        extension_repo.finish_impeachment_sync,
        imp_id, votes, agree, disagree,
        "passed" if passed else "rejected", replacement_ai_id)

    event = {
        "impeachment_id": imp_id,
        "votes": votes,
        "agree_count": agree,
        "disagree_count": disagree,
        "result": "passed" if passed else "rejected",
        "replacement_ai_id": replacement_ai_id,
    }
    await sse_manager.publish(stream_id, "impeachment_result", event)

    # ---- 弹劾通过：直接改裁判表，让换人真正生效 ----
    # 只写弹劾记录表的话，引擎取裁判仍走 get_judge_sync，裁判纹丝不动。
    # 改 participants = 单一数据源：下一轮自动用新裁判，
    # 详情接口与 judge_scoring / judge_info 等事件里的裁判信息同步更新。
    if passed and replacement_ai_id:
        replaced = await asyncio.to_thread(
            discussion_repo.update_judge_sync,
            discussion_id, target_role, replacement_ai_id)
        if replaced:
            role_label = JUDGE_ROLE_LABELS.get(target_role, target_role)
            old_name = _display_name(target_ai_id)
            new_name = _display_name(replacement_ai_id)
            await sse_manager.publish(stream_id, "judge_replaced", {
                "impeachment_id": imp_id,
                "judge_type": target_role,
                "judge_role": role_label,
                "old_ai_id": target_ai_id,
                "old_display_name": old_name,
                "new_ai_id": replacement_ai_id,
                "new_display_name": new_name,
                "message": f"{role_label}已由 {old_name} 替换为 {new_name}",
            })
            event["replaced"] = True
        else:
            logger.warning(
                "弹劾通过但裁判替换失败: discussion=%s role=%s -> %s",
                discussion_id, target_role, replacement_ai_id)
            event["replaced"] = False

    return event


def get_judge_override(discussion_id: int, role: str) -> Optional[str]:
    """
    查询该讨论某裁判角色是否被弹劾替换。返回替补 ai_id 或 None。

    注意：换人现已直接落到 discussion_participants（单一数据源，
    update_judge_sync），引擎取裁判只读那张表。本函数仅用于回看
    「历史上某场讨论换过谁」，不要再拿它做运行时裁决——否则会出现
    两套数据源不一致。
    """
    records = extension_repo.list_impeachments_sync(discussion_id)
    for r in records:
        if (r["target_judge_role"] == role
                and r["result"] == "passed"
                and r.get("replacement_ai_id")):
            return r["replacement_ai_id"]
    return None


# ============================================================
# 模块E：认知蒸馏
# ============================================================

async def distill_discussion(discussion_id: int, stream_id: str) -> Dict[str, Any]:
    """
    认知蒸馏：
      1. 每位讨论者输出13部分系统总结
      2. 裁判输出权威总结
      3. 新旧认知框架对比 → 框架演化
    跳过条件：
      - style_mode 为 planning / affective → 跳过蒸馏（不做总结与记忆）
      - 有效发言数 < 3 条 → 跳过
    """
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id)
    if discussion is None:
        return {"error": "讨论不存在"}

    # 0. 模式判断：planning/affective 跳过总结与记忆
    style_mode = (discussion.get("style_mode") or "free").lower()
    if style_mode in ("planning", "affective"):
        reason = f"讨论模式为 {style_mode}，已跳过总结与记忆"
        logger.info("蒸馏跳过（模式）: discussion=%s style_mode=%s",
                    discussion_id, style_mode)
        await sse_manager.publish(stream_id, "distill_skip", {
            "reason": "mode_skip", "message": reason})
        return {"skipped": True, "reason": reason}

    participants = await asyncio.to_thread(
        discussion_repo.get_discussants_sync, discussion_id)
    discussants = participants

    messages = await asyncio.to_thread(
        discussion_repo.get_recent_messages_sync, discussion_id, 200)
    transcript = "\n\n".join(
        f"[{m.get('display_name', m.get('ai_id'))}]: {m.get('content', '')[:400]}"
        for m in messages) or "（无发言）"

    # 0.5 有效发言数检查（≥3 条）
    valid_count = sum(
        1 for m in messages
        if (m.get("content") or "").strip()
    )
    if valid_count < 3:
        reason = f"有效发言仅 {valid_count} 条，至少需要 3 条"
        logger.info("蒸馏跳过（消息数不足）: discussion=%s count=%s",
                    discussion_id, valid_count)
        await sse_manager.publish(stream_id, "distill_skip", {
            "reason": "too_few_messages", "message": reason,
            "valid_count": valid_count})
        return {"skipped": True, "reason": reason,
                "valid_count": valid_count}

    # 1. 各讨论者13部分总结（按工作流程，取前 3 位：ai1/ai2/ai3）
    summaries: List[Dict[str, str]] = []
    for p in discussants[:3]:
        if sse_manager.is_cancelled(stream_id):
            await sse_manager.publish(stream_id, "distill_cancelled", {})
            return {"cancelled": True, "summaries": summaries}
        r = await call_ai(
            p["ai_id"],
            [
                {"role": "system", "content": DISTILL_INDIVIDUAL_PROMPT},
                {"role": "user", "content": transcript[:6000]},
            ],
            task_type="discussion", task_id=discussion_id,
            max_tokens=1536, temperature=0.4)
        if r.status == "success" and r.content:
            summaries.append({"ai_id": p["ai_id"],
                              "display_name": p["display_name"],
                              "summary": r.content.strip()})
            # 写入后端记忆库（同讨论+同AI只保留最新一份）
            memory_id = await memory_service.save_discussion_summary_memory(
                discussion, p["ai_id"], p["display_name"], r.content.strip())
            await sse_manager.publish(stream_id, "distill_summary", {
                "ai_id": p["ai_id"], "display_name": p["display_name"],
                "summary": r.content.strip(), "memory_id": memory_id})
        else:
            # 失败显式上报，前端可显示「生成失败」
            err = getattr(r, "error_message", "") or "调用失败"
            logger.warning(
                "蒸馏单点总结失败: discussion=%s ai_id=%s err=%s",
                discussion_id, p["ai_id"], err)
            await sse_manager.publish(stream_id, "distill_summary_failed", {
                "ai_id": p["ai_id"],
                "display_name": p["display_name"],
                "error": err,
            })

    # 2. 裁判总结
    if sse_manager.is_cancelled(stream_id):
        await sse_manager.publish(stream_id, "distill_cancelled", {})
        return {"cancelled": True, "summaries": summaries}
    judge_result = await _generate_judge_summary(discussion, stream_id, transcript)
    if judge_result["cancelled"]:
        return {"cancelled": True, "summaries": summaries}
    judge_ai = judge_result["judge_ai"] or DISCUSSION_JUDGES["evaluation"]["ai_id"]
    judge_summary = judge_result["summary"]
    if judge_summary:
        await asyncio.to_thread(
            discussion_repo.update_discussion_judge_summary_sync,
            discussion_id, judge_summary)
        # 落记忆库：切成四节结构，供前端可视化卡片与检索使用
        saved = await memory_service.save_judge_summary_memory(
            discussion, judge_ai, judge_summary)
        await sse_manager.publish(stream_id, "distill_judge", {
            "judge_ai": judge_ai, "summary": judge_summary,
            "memory_id": saved["memory_id"], "sections": saved["sections"],
            "topic": discussion.get("topic")})

    # 3. 框架演化
    old_frame = await asyncio.to_thread(extension_repo.get_active_frame_sync)
    new_frame_text = judge_summary or (summaries[0]["summary"] if summaries else "")
    frame_result: Dict[str, Any] = {"updated": False}
    if new_frame_text:
        if old_frame:
            r = await call_ai(
                judge_ai,
                [{"role": "user", "content": build_prompt(
                    DISTILL_FRAME_EVOLVE,
                    old_frame=old_frame["frame_text"][:2000],
                    new_frame=new_frame_text[:2000])}],
                task_type="judge", task_id=discussion_id,
                max_tokens=2048, temperature=0.3)
            if r.status == "success":
                data = _extract_json_obj(r.content or "")
                if data and data.get("is_better") and data.get("updated_frame"):
                    await asyncio.to_thread(
                        extension_repo.retire_frame_sync, old_frame["id"])
                    new_id = await asyncio.to_thread(
                        extension_repo.insert_frame_sync,
                        data["updated_frame"], topic=discussion["topic"],
                        source_discussion_id=discussion_id,
                        generation=old_frame["generation"] + 1)
                    frame_result = {"updated": True, "frame_id": new_id,
                                    "generation": old_frame["generation"] + 1}
        else:
            new_id = await asyncio.to_thread(
                extension_repo.insert_frame_sync, new_frame_text[:4000],
                topic=discussion["topic"],
                source_discussion_id=discussion_id, generation=1)
            frame_result = {"updated": True, "frame_id": new_id, "generation": 1}

        if frame_result["updated"]:
            await sse_manager.publish(stream_id, "frame_evolved", frame_result)

    return {
        "summaries": summaries,
        "judge_summary": judge_summary,
        "frame": frame_result,
    }


def _resolve_judge_candidates(
    discussion: Dict[str, Any],
    participants: List[Dict[str, Any]],
    preferred: Optional[str] = None,
) -> List[str]:
    """
    裁判模型候选，严格按工作流程的优先级排列：
      1. 前端指定的独立裁判（currentJudgeId / 独立裁判下拉框）
      2. 配置的评价裁判 + 其 fallback 链
      3. 讨论参与者中的第一个
      4. 任意已启用模型
    """
    raw: List[str] = []
    if preferred:
        raw.append(preferred)
    cfg = DISCUSSION_JUDGES.get("evaluation") or {}
    raw.append(cfg.get("ai_id"))
    raw.extend(cfg.get("fallback") or [])
    raw.extend(p.get("ai_id") for p in participants)
    raw.extend(_available_model_ids() or list(AI_MODELS.keys()))

    out: List[str] = []
    for item in raw:
        if item and item not in out:
            out.append(item)
    return out


async def _generate_judge_summary(
    discussion: Dict[str, Any],
    stream_id: str,
    transcript: str,
    preferred: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成裁判总结：按候选优先级逐个尝试，任一成功即返回。

    单个裁判模型不可用（超时/额度/被封禁）时自动降级，
    避免「裁判总结」按钮直接失败。

    返回 {"cancelled": bool, "judge_ai": str|None, "summary": str, "error": str|None}
    """
    discussion_id = discussion.get("id")

    # 前置检查：已被封禁的参与者不进入裁判候选（工作流程①）
    participants = discussion.get("participants")
    if participants is None:
        participants = await asyncio.to_thread(
            discussion_repo.get_discussants_sync, discussion_id)
    participants = [p for p in (participants or []) if not p.get("is_banned")]

    candidates = _resolve_judge_candidates(discussion, participants, preferred)
    if not candidates:
        return {"cancelled": False, "judge_ai": None, "summary": "",
                "error": "无可用裁判模型"}

    last_err = "调用失败"
    for judge_ai in candidates:
        if sse_manager.is_cancelled(stream_id):
            await sse_manager.publish(stream_id, "distill_cancelled", {})
            return {"cancelled": True, "judge_ai": None, "summary": "", "error": None}

        r = await call_ai(
            judge_ai,
            [{"role": "user", "content": build_prompt(
                DISTILL_JUDGE_PROMPT,
                topic=discussion.get("topic"), transcript=transcript[:6000])}],
            task_type="judge", task_id=discussion_id,
            max_tokens=2048, temperature=0.3)
        if r.status == "success" and (r.content or "").strip():
            return {"cancelled": False, "judge_ai": judge_ai,
                    "summary": r.content.strip(), "error": None}

        last_err = getattr(r, "error_message", "") or "调用失败"
        logger.warning("裁判 %s 生成总结失败，降级至下一个候选: %s", judge_ai, last_err)

    return {"cancelled": False, "judge_ai": None, "summary": "", "error": last_err}


async def judge_summary_only(
    discussion_id: int,
    stream_id: str,
    preferred_judge: Optional[str] = None,
) -> Dict[str, Any]:
    """
    辅助流程：仅生成裁判总结，跳过单AI总结与框架演化。
    用于前端"裁判总结"快捷按钮。

    preferred_judge：前端指定的独立裁判 ID（最高优先级）。
    """
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id)
    if discussion is None:
        return {"error": "讨论不存在"}

    style_mode = (discussion.get("style_mode") or "free").lower()
    if style_mode in ("planning", "affective"):
        reason = f"讨论模式为 {style_mode}，已跳过裁判总结"
        await sse_manager.publish(stream_id, "distill_skip", {
            "reason": "mode_skip", "message": reason})
        return {"skipped": True, "reason": reason}

    messages = await asyncio.to_thread(
        discussion_repo.get_recent_messages_sync, discussion_id, 200)
    valid_count = sum(1 for m in messages if (m.get("content") or "").strip())
    if valid_count < 3:
        reason = f"有效发言仅 {valid_count} 条，至少需要 3 条"
        await sse_manager.publish(stream_id, "distill_skip", {
            "reason": "too_few_messages", "message": reason,
            "valid_count": valid_count})
        return {"skipped": True, "reason": reason, "valid_count": valid_count}

    transcript = "\n\n".join(
        f"[{m.get('display_name', m.get('ai_id'))}]: {m.get('content', '')[:400]}"
        for m in messages) or "（无发言）"

    result = await _generate_judge_summary(
        discussion, stream_id, transcript, preferred=preferred_judge)
    if result["cancelled"]:
        return {"cancelled": True}
    if not result["judge_ai"]:
        await sse_manager.publish(stream_id, "distill_judge_failed",
                                  {"judge_ai": None, "error": result["error"]})
        return {"error": result["error"]}
    judge_ai = result["judge_ai"]
    judge_summary = result["summary"]
    await asyncio.to_thread(
        discussion_repo.update_discussion_judge_summary_sync,
        discussion_id, judge_summary)
    # 落记忆库：切成四节结构，前端据此外发结构化可视化卡片
    saved = await memory_service.save_judge_summary_memory(
        discussion, judge_ai, judge_summary)
    await sse_manager.publish(stream_id, "distill_judge", {
        "judge_ai": judge_ai, "summary": judge_summary,
        "memory_id": saved["memory_id"], "sections": saved["sections"],
        "topic": discussion.get("topic")})
    return {
        "judge_summary": judge_summary,
        "judge_ai": judge_ai,
        "memory_id": saved["memory_id"],
    }


# ============================================================
# 模块F：用户信用治理
# ============================================================

async def governance_roundtable(trigger_reason: str) -> Dict[str, Any]:
    """
    AI 圆桌会议：集体评议用户行为，裁决信用分变动。
    信用分 < 60 时自动触发。
    """
    credit = await asyncio.to_thread(extension_repo.get_credit_sync)
    behaviors = await asyncio.to_thread(
        extension_repo.list_recent_behavior_sync, limit=10)
    behavior_lines = "\n".join(
        f"- {b['action']}: {b['detail']}（Δ{b['credit_delta']}）"
        for b in behaviors) or "（无记录）"

    council = _pick_chat_models()
    verdicts: List[Dict[str, Any]] = []
    for m in council:
        prompt = build_prompt(
            GOV_ROUNDTABLE_PROMPT,
            trigger_reason=trigger_reason,
            credit_score=credit["credit_score"],
            behavior_logs=behavior_lines,
            credit_history=f"当前 {credit['credit_score']} 分",
            appeals="（无）",
        )
        r = await call_ai(
            m.ai_id, [{"role": "user", "content": prompt}],
            task_type="system_check", max_tokens=256, temperature=0.3)
        if r.status == "success":
            data = _extract_json_obj(r.content or "")
            if data:
                verdicts.append(data)

    if not verdicts:
        return {"verdict": "无法裁决", "penalty": 0, "restrictions": [],
                "reason": "圆桌会议未产出有效裁决"}

    # 多数决
    valid = [v for v in verdicts if v.get("verdict") in ("合理", "警告", "不合理")]
    if not valid:
        return {"verdict": "无法裁决", "penalty": 0, "restrictions": [],
                "reason": "圆桌会议裁决格式异常"}
    counts = {k: sum(1 for v in valid if v["verdict"] == k)
              for k in ("合理", "警告", "不合理")}
    final_verdict = max(counts, key=counts.get)

    penalty = sum(float(v.get("penalty", 0)) for v in valid) / len(valid)
    restrictions: List[str] = []
    for v in valid:
        for item in (v.get("restrictions") or []):
            if item and item not in restrictions:
                restrictions.append(item)

    # 应用裁决
    await asyncio.to_thread(
        extension_repo.apply_credit_sync, "default", -penalty, restrictions)

    return {
        "verdict": final_verdict,
        "penalty": round(penalty, 1),
        "restrictions": restrictions,
        "reason": "; ".join(str(v.get("reason", "")) for v in valid[:3]),
        "votes": counts,
    }


async def record_thumbs_down(target: str, detail: str) -> Dict[str, Any]:
    """点踩 → 记录 + 信用分 -2；扣分后 <60 触发圆桌会议。"""
    await asyncio.to_thread(
        extension_repo.log_user_action_sync, "default",
        "thumbs_down", f"{target}: {detail}", credit_delta=-2.0)
    credit = await asyncio.to_thread(extension_repo.get_credit_sync)
    result = {"credit_score": credit["credit_score"], "roundtable": None}
    if credit["credit_score"] < 60:
        result["roundtable"] = await governance_roundtable(
            f"信用分降至 {credit['credit_score']}，自动触发圆桌评议")
    return result
