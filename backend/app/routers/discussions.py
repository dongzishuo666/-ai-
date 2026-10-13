# ============================================================
# 文件：backend/app/routers/discussions.py
# 说明：功能二（多 AI 讨论 + 裁判监管）路由。
# 状态：批次1 → 实现 POST /create（仅入库 + 11 条 participants，不启动任务）
# ============================================================

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query

from app.config import AI_MODELS
from app.config.business import (
    DISCUSSION_CONFIG,
    DISCUSSION_DEFAULT_PARTICIPANTS,
    DISCUSSION_JUDGES,
    DISCUSSION_PARTICIPANT_COUNT,
    DISCUSSION_PARTICIPANTS,
)
from app.services.ai_health import probe_all_models
from app.database import discussion_repo
from app.middleware.error_handler import AppException
from app.models.schemas import (
    ApiResponse,
    DiscussionCreateData,
    DiscussionCreateRequest,
    JudgeItem,
    ParticipantItem,
)

logger = logging.getLogger("app.routers.discussions")

router = APIRouter(prefix="/api/discussions", tags=["discussions"])


# ------------------------------------------------------------
# 响应组装：participants / judges
# ------------------------------------------------------------

# 裁判角色元数据（与 repo 中 _JUDGE_META 一致；此处用于响应拼装）
_JUDGE_META = {
    "compliance": {"display_name": "实时监督裁判", "duty": "发言合规检测"},
    "order":      {"display_name": "秩序裁判",     "duty": "顺序管控与计分"},
    "evaluation": {"display_name": "最终评价裁判", "duty": "终局评估与总结"},
}


async def _build_participants_response(discussion_id: int) -> list[ParticipantItem]:
    """从数据库读取本次讨论实际出场 discussant（3 位）。"""
    rows = await asyncio.to_thread(
        discussion_repo.get_discussants_sync, discussion_id
    )
    return [
        ParticipantItem(
            ai_id=r["ai_id"],
            display_name=r["display_name"],
            channel=r["channel"],
            role="discussant",
        )
        for r in rows
    ]


async def _build_judges_response(discussion_id: int) -> list[JudgeItem]:
    """从数据库读取本次讨论的 3 位裁判。"""
    rows = await asyncio.to_thread(
        discussion_repo.get_participants_sync, discussion_id
    )
    result = []
    for r in rows:
        if r.get("role") != "judge":
            continue
        judge_type = r.get("judge_type") or ""
        meta = _JUDGE_META.get(judge_type, {})
        cfg = AI_MODELS.get(r["ai_id"])
        result.append(
            JudgeItem(
                judge_id=judge_type,
                display_name=meta.get("display_name", r["display_name"]),
                model=r["ai_id"],
                duty=meta.get("duty", ""),
                model_display=cfg.display_name if cfg else r["ai_id"],
            )
        )
    return result


# ------------------------------------------------------------
# POST /create
# ------------------------------------------------------------

async def _pick_online_participants() -> Optional[list[str]]:
    """
    未指定阵容时，挑选当前在线的讨论者。

    默认阵容以中转渠道模型为主（gemini/claude），中转站额度耗尽时这些席位
    会全程失败——而讨论者不降级（避免身份混乱），等于整场空转。
    这里先剔除离线模型再从候选池补齐；中转恢复后会自动用回默认阵容。
    """
    try:
        status = await probe_all_models()
        online = {
            m.get("ai_id")
            for m in (status.get("models") or [])
            if m.get("status") in ("online", "degraded")
        }
    except Exception:
        return None          # 探活异常时回落配置默认值

    if not online:
        return None

    picked = [a for a in DISCUSSION_DEFAULT_PARTICIPANTS if a in online]
    for a in DISCUSSION_PARTICIPANTS:
        if len(picked) >= DISCUSSION_PARTICIPANT_COUNT:
            break
        if a in online and a not in picked:
            picked.append(a)
    if not picked:
        return None
    return picked[:DISCUSSION_PARTICIPANT_COUNT]


@router.post("/create", response_model=ApiResponse)
async def create_discussion(payload: DiscussionCreateRequest) -> ApiResponse:
    """
    创建讨论。
    - 仅入库 discussions + 11 条 participants（status='created'）
    - 不启动流水线（由 GET /{id}/stream 触发）
    - 若已有非终结态讨论，返回 409 TASK_BUSY
    """
    # 1. 并发防护
    #    先回收僵尸：一次崩溃/断连遗留的 running 记录会永久占用单任务锁，
    #    此后所有新建都会被 409 拒绝，只能人工改库才能恢复。
    reaped = await asyncio.to_thread(
        discussion_repo.reap_stale_discussions_sync,
        int(DISCUSSION_CONFIG.get("stale_task_seconds", 1800)),
    )
    if reaped:
        logger.warning("自动回收僵尸讨论 %s 条（长时间无进展）", reaped)

    active = await asyncio.to_thread(
        discussion_repo.count_active_discussions_sync
    )
    if active > 0:
        raise AppException(
            code="TASK_BUSY",
            message="已有讨论任务处于活跃状态，请等待完成或终止后再创建",
            http_status=409,
        )

    # 2. 入库（单事务：discussion + 3 discussant + 3 judge）
    #    未指定阵容时剔除离线模型：讨论者不降级，留着离线席位只会全程空转
    participant_ids = getattr(payload, "participants", None)
    if not participant_ids:
        participant_ids = await _pick_online_participants()

    row = await asyncio.to_thread(
        discussion_repo.insert_discussion_with_participants_sync,
        topic=payload.topic,
        mode=payload.mode,
        total_rounds=payload.rounds,
        max_duration_seconds=payload.max_duration_seconds,
        style_mode=getattr(payload, "style_mode", "free"),
        until_consensus=getattr(payload, "until_consensus", False),
        memory_enabled=getattr(payload, "memory_enabled", False),
        participant_ids=participant_ids,
        judge_map=getattr(payload, "judges", None),
        consensus_strictness=getattr(payload, "consensus_strictness", "medium"),
    )

    discussion_id = row["discussion_id"]

    # 3. 组装响应
    data = DiscussionCreateData(
        discussion_id=discussion_id,
        topic=row["topic"],
        status=row["status"],
        total_rounds=row["total_rounds"],
        mode=row["mode"],
        participants=await _build_participants_response(discussion_id),
        judges=await _build_judges_response(discussion_id),
        stream_url=f"/api/discussions/{discussion_id}/stream",
        created_at=row["created_at"],
    )
    logger.info(
        "讨论创建成功：id=%s topic=%s rounds=%s mode=%s",
        discussion_id, payload.topic, payload.rounds, payload.mode,
    )
    return ApiResponse(data=data.model_dump())


import json
from typing import AsyncIterator

from fastapi.responses import StreamingResponse

from app.config import SSE_HEARTBEAT_INTERVAL
from app.models.schemas import (
    DiscussionPauseData,
    DiscussionResumeData,
    DiscussionTerminateData,
)
from app.services.discussion_engine import (
    _pause_watchdog,
    _pause_watchdogs,
    is_paused,
    mark_paused,
    mark_resumed,
    run_discussion_pipeline,
)
from app.services.sse_manager import sse_manager
from datetime import datetime, timezone


# ============================================================
# 追加：批次2 · SSE / pause / resume / terminate
# ============================================================

_bg_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- SSE 生成器 ----------

async def _sse_generator(queue: asyncio.Queue, stream_id: str) -> AsyncIterator[str]:
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=SSE_HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            event = item.get("event")
            data = item.get("data", {})
            if event == "__close__":
                return
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if event == "done":
                return
    finally:
        sse_manager.unregister(stream_id)


async def _sse_terminal_discussion(discussion_id: int, status: str) -> AsyncIterator[str]:
    """终态连接：done/terminated/failed → 读库并推 discussion_end + done。"""
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    rounds_completed = (discussion or {}).get("rounds_completed", 0)
    if status == "done":
        leaderboard = await asyncio.to_thread(
            discussion_repo.get_leaderboard_sync, discussion_id,
        )
        final_ranking = []
        for item in leaderboard:
            total = item["total_score"]
            avg = round(total / max(1, rounds_completed), 1)
            final_ranking.append({
                "rank": item["rank"],
                "ai_id": item["ai_id"],
                "display_name": item["display_name"],
                "total_score": total,
                "avg_score": avg,
            })
        payload = {
            "discussion_id": discussion_id,
            "status": "done",
            "end_reason": (discussion or {}).get("end_reason") or "max_rounds",
            "total_rounds_completed": rounds_completed,
            "total_duration_ms": (discussion or {}).get("total_duration_ms", 0),
            "final_ranking": final_ranking,
        }
        yield f"event: discussion_end\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        # 评价裁判（若已落库）
        summary = (discussion or {}).get("judge_summary")
        if summary:
            try:
                parsed = json.loads(summary)
                yield f"event: judge_evaluation\ndata: {json.dumps(parsed, ensure_ascii=False)}\n\n"
            except Exception:
                pass
        action_plan = (discussion or {}).get("action_plan")
        if action_plan:
            yield f"event: action_plan\ndata: {json.dumps({'format':'markdown','content':action_plan}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"
    else:
        msg = (discussion or {}).get("error_message") or "讨论已终止"
        yield f"event: error\ndata: {json.dumps({'message': msg, 'code': 'INTERNAL_ERROR'}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"


# ---------- GET /{id}/stream ----------

@router.get("/{discussion_id}/stream")
async def stream_discussion(discussion_id: int):
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在", http_status=404)

    status = discussion["status"]
    if status in ("done", "terminated", "failed"):
        return StreamingResponse(
            _sse_terminal_discussion(discussion_id, status),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    stream_id = f"discussion_{discussion_id}"

    if sse_manager.is_registered(stream_id):
        raise AppException(
            code="TASK_BUSY",
            message="该讨论已有活跃的 SSE 连接",
            http_status=409,
        )

    queue = sse_manager.register(stream_id)

    if status == "created":
        ok = await asyncio.to_thread(
            discussion_repo.update_discussion_status_sync,
            discussion_id, "running",
            expected_status="created",
            touch_started=True,
        )
        if not ok:
            sse_manager.unregister(stream_id)
            raise AppException(
                code="TASK_BUSY",
                message="讨论已被其他连接启动",
                http_status=409,
            )
        _spawn(run_discussion_pipeline(discussion_id))

    return StreamingResponse(
        _sse_generator(queue, stream_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ---------- POST /pause ----------

@router.post("/{discussion_id}/pause", response_model=ApiResponse)
async def pause_discussion(discussion_id: int) -> ApiResponse:
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在", http_status=404)
    if discussion["status"] != "running":
        raise AppException(
            code="VALIDATION_ERROR",
            message=f"讨论状态为 {discussion['status']}，仅 running 可暂停",
            http_status=400,
        )
    if is_paused(discussion_id):
        raise AppException(
            code="VALIDATION_ERROR",
            message="讨论已处于暂停状态",
            http_status=400,
        )

    ok = await asyncio.to_thread(
        discussion_repo.update_discussion_status_sync,
        discussion_id, "paused",
        expected_status="running",
        touch_paused=True,
    )
    if not ok:
        raise AppException(
            code="TASK_BUSY", message="暂停失败，状态已变更", http_status=409,
        )

    # 内存态标记
    mark_paused(discussion_id)

    # 启动暂停超时 watchdog
    stream_id = f"discussion_{discussion_id}"
    wd = asyncio.create_task(_pause_watchdog(discussion_id, stream_id))
    _pause_watchdogs[discussion_id] = wd

    # 推送暂停事件
    messages_count = await asyncio.to_thread(
        discussion_repo.count_messages_sync, discussion_id,
    )
    await sse_manager.publish(stream_id, "discussion_paused", {
        "discussion_id": discussion_id,
        "paused_at": _utc_now(),
        "current_round": discussion["current_round"],
        "messages_so_far": messages_count,
        "message": "讨论已暂停，等待恢复",
    })

    data = DiscussionPauseData(
        discussion_id=discussion_id,
        status="paused",
        paused_at=_utc_now(),
        current_round=discussion["current_round"],
        messages_so_far=messages_count,
    )
    return ApiResponse(data=data.model_dump())


# ---------- POST /resume ----------

@router.post("/{discussion_id}/resume", response_model=ApiResponse)
async def resume_discussion(discussion_id: int) -> ApiResponse:
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在", http_status=404)
    if discussion["status"] != "paused":
        raise AppException(
            code="VALIDATION_ERROR",
            message=f"讨论状态为 {discussion['status']}，仅 paused 可恢复",
            http_status=400,
        )

    ok = await asyncio.to_thread(
        discussion_repo.update_discussion_status_sync,
        discussion_id, "running",
        expected_status="paused",
        touch_resumed=True,
    )
    if not ok:
        raise AppException(
            code="TASK_BUSY", message="恢复失败，状态已变更", http_status=409,
        )

    mark_resumed(discussion_id)

    stream_id = f"discussion_{discussion_id}"
    await sse_manager.publish(stream_id, "discussion_resumed", {
        "discussion_id": discussion_id,
        "resumed_at": _utc_now(),
        "resume_round": discussion["current_round"],
        "message": "讨论已恢复",
    })

    data = DiscussionResumeData(
        discussion_id=discussion_id,
        status="running",
        resumed_at=_utc_now(),
        resume_round=discussion["current_round"],
        stream_url=f"/api/discussions/{discussion_id}/stream",
    )
    return ApiResponse(data=data.model_dump())


# ---------- POST /terminate ----------

@router.post("/{discussion_id}/terminate", response_model=ApiResponse)
async def terminate_discussion(discussion_id: int) -> ApiResponse:
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    if discussion is None:
        raise AppException(code="NOT_FOUND", message="讨论不存在", http_status=404)

    if discussion["status"] not in ("running", "paused", "created"):
        raise AppException(
            code="VALIDATION_ERROR",
            message=f"讨论状态为 {discussion['status']}，仅 running/paused/created 可终止",
            http_status=400,
        )

    # 若暂停中，先解除内存态
    if is_paused(discussion_id):
        mark_resumed(discussion_id)

    ok = await asyncio.to_thread(
        discussion_repo.update_discussion_status_sync,
        discussion_id, "terminated",
        expected_status=discussion["status"],
        end_reason="user_manual",
        completed=True,
    )
    if not ok:
        raise AppException(
            code="TASK_BUSY", message="终止失败，状态已变更", http_status=409,
        )

    data = DiscussionTerminateData(
        discussion_id=discussion_id,
        status="terminated",
        end_reason="user_manual",
        terminated_at=_utc_now(),
        rounds_completed=discussion["rounds_completed"],
    )
    return ApiResponse(data=data.model_dump())


from app.config.business import DISCUSSION_PARTICIPANTS
from typing import Optional


# ============================================================
# 追加：批次3 · 详情 / 列表 / 删除
# ============================================================

# ------------------------------------------------------------
# 辅助：组装详情响应
# ------------------------------------------------------------

def _split_participants(rows: list) -> tuple:
    """
    将 participants 记录按 role 拆分为 (discussants, judges)。
    - discussants：蓝图 2.1 的 participants 数组（含 ai_id/display_name/channel/role）
    - judges：蓝图 2.1 的 judges 数组（含 judge_id/display_name/model/duty）
    """
    discussants = []
    judges = []
    for r in rows:
        if r["role"] == "discussant":
            discussants.append({
                "seat_id": r.get("id"),
                "ai_id": r["ai_id"],
                "display_name": r["display_name"],
                "channel": r["channel"],
                "role": "discussant",
                # 历史查看 / 断线重连要靠这些字段还原计分榜与禁言状态，
                # 缺了它们前端会把总分全显示成 0、禁言卡片永远不出现
                "total_score": float(r.get("total_score") or 0.0),
                "final_rank": r.get("final_rank"),
                "is_banned": bool(r.get("is_banned")),
                "ban_reason": r.get("ban_reason") or "",
            })
        elif r["role"] == "judge":
            meta = _JUDGE_META.get(r["judge_type"], {})
            cfg = AI_MODELS.get(r["ai_id"])
            judges.append({
                "judge_id": r["judge_type"],
                "display_name": meta.get("display_name", r["display_name"]),
                "model": r["ai_id"],
                "duty": meta.get("duty", ""),
                "model_display": cfg.display_name if cfg else r["ai_id"],
            })
    return discussants, judges


def _build_messages_response(rows: list) -> list:
    """
    组装 messages 数组。
    裁定/规范要求：不含每消息评分（weighted_total / scores 不外露）。
    """
    result = []
    for r in rows:
        result.append({
            "message_id": r["message_id"],
            "round": r["round"],
            "order": r["message_order"],
            "ai_id": r["ai_id"],
            "display_name": r["display_name"],
            "content": r["content"],
            "word_count": r["word_count"],
            "compliance_status": r["compliance_status"],
            "compliance_reason": r["compliance_reason"],
            "violation_type": r["violation_type"],
            "timestamp": r["created_at"],
        })
    return result


def _build_final_ranking(discussion: dict, leaderboard: list) -> list:
    """
    组装最终排名（含 avg_score）。
    - 若讨论未结束，rounds_completed=0 时 avg=0
    """
    rounds_completed = discussion.get("rounds_completed") or 0
    result = []
    for item in leaderboard:
        total = item["total_score"]
        avg = round(total / max(1, rounds_completed), 1)
        result.append({
            "rank": item["rank"],
            "ai_id": item["ai_id"],
            "display_name": item["display_name"],
            "total_score": total,
            "avg_score": avg,
        })
    return result


def _parse_judge_summary(raw: Optional[str]) -> Optional[dict]:
    """judge_summary 存储为 JSON 字符串，解析为对象；失败/空返回 None。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ------------------------------------------------------------
# GET /{discussion_id} · 详情
# ------------------------------------------------------------

@router.get("/{discussion_id}", response_model=ApiResponse)
async def get_discussion_detail(discussion_id: int) -> ApiResponse:
    """
    获取讨论详情（含完整 messages、最终排名、评价总结、行动方案）。
    不存在返回 404 NOT_FOUND。
    """
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    if discussion is None:
        raise AppException(
            code="NOT_FOUND",
            message="讨论不存在",
            http_status=404,
        )

    # 并行读取（单 worker 下 to_thread 顺序执行，无并行收益，但语义清晰）
    participants_rows = await asyncio.to_thread(
        discussion_repo.get_participants_sync, discussion_id,
    )
    messages_rows = await asyncio.to_thread(
        discussion_repo.get_all_messages_sync, discussion_id,
    )
    leaderboard = await asyncio.to_thread(
        discussion_repo.get_leaderboard_sync, discussion_id,
    )
    round_scores_rows = await asyncio.to_thread(
        discussion_repo.get_all_round_scores_sync, discussion_id,
    )

    discussants, judges = _split_participants(participants_rows)

    # 按轮次聚合评分，供历史查看 / 断线重连还原「第 N 轮」评分表
    round_scores: dict = {}
    for r in round_scores_rows:
        round_scores.setdefault(r["round"], []).append({
            "ai_id": r["ai_id"],
            "display_name": r["display_name"],
            "quality": r["quality"],
            "relevance": r["relevance"],
            "contribution": r["contribution"],
            "compliance": r["compliance"],
            "weighted_total": r["weighted_total"],
            "absent": False,
        })

    # 仅终态讨论返回 final_ranking；运行中返回空数组（前端可用 scoreboard 事件渲染实时榜）
    status = discussion["status"]
    if status in ("done", "terminated"):
        final_ranking = _build_final_ranking(discussion, leaderboard)
    else:
        final_ranking = []

    data = {
        "discussion_id": discussion["discussion_id"],
        "topic": discussion["topic"],
        "status": status,
        "end_reason": discussion["end_reason"],
        "mode": discussion["mode"],
        "total_rounds": discussion["total_rounds"],
        "current_round": discussion["current_round"],
        "rounds_completed": discussion["rounds_completed"],
        "total_duration_ms": discussion["total_duration_ms"],
        "participants": discussants,
        "judges": judges,
        "messages": _build_messages_response(messages_rows),
        "round_scores": round_scores,
        "final_ranking": final_ranking,
        "judge_evaluation": _parse_judge_summary(discussion["judge_summary"]),
        "action_plan": discussion["action_plan"],
        # 局中总结（JSON 文本）：发散期结束后由评价裁判产出，供历史查看时还原
        "midterm_summary": discussion.get("midterm_summary"),
        "error_message": discussion["error_message"],
        "created_at": discussion["created_at"],
        "started_at": discussion["started_at"],
        "paused_at": discussion["paused_at"],
        "resumed_at": discussion["resumed_at"],
        "completed_at": discussion["completed_at"],
    }
    return ApiResponse(data=data)


# ------------------------------------------------------------
# GET "" · 列表
# ------------------------------------------------------------

@router.get("", response_model=ApiResponse)
async def list_discussions(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(20, ge=1, le=50, description="每页条数，最大50"),
) -> ApiResponse:
    """
    讨论历史列表（分页）。
    每项含 winner（done 状态下 rank=1 的 AI）。
    """
    result = await asyncio.to_thread(
        discussion_repo.list_discussions_sync, page, page_size,
    )

    # 为每条记录补充 winner（仅 done/terminated 状态下查询）
    items = result["items"]
    enriched_items = []
    for item in items:
        entry = {
            "discussion_id": item["discussion_id"],
            "topic": item["topic"],
            "mode": item["mode"],
            "status": item["status"],
            "end_reason": item["end_reason"],
            "rounds_completed": item["rounds_completed"],
            "total_rounds": item["total_rounds"],
            "created_at": item["created_at"],
            "completed_at": item["completed_at"],
        }
        # winner 仅在终态且有排名时返回
        if item["status"] in ("done", "terminated"):
            leaderboard = await asyncio.to_thread(
                discussion_repo.get_leaderboard_sync, item["discussion_id"],
            )
            if leaderboard:
                top = leaderboard[0]
                entry["winner"] = {
                    "ai_id": top["ai_id"],
                    "display_name": top["display_name"],
                }
            else:
                entry["winner"] = None
        else:
            entry["winner"] = None
        enriched_items.append(entry)

    result["items"] = enriched_items
    return ApiResponse(data=result)


# ------------------------------------------------------------
# DELETE /{discussion_id}
# ------------------------------------------------------------

@router.delete("/{discussion_id}", response_model=ApiResponse)
async def delete_discussion(discussion_id: int) -> ApiResponse:
    """
    删除讨论（含全部子表，靠 CASCADE）。
    约束：仅允许 done / terminated / failed；其他状态返回 409 TASK_BUSY。
    """
    discussion = await asyncio.to_thread(
        discussion_repo.get_discussion_sync, discussion_id,
    )
    if discussion is None:
        raise AppException(
            code="NOT_FOUND",
            message="讨论不存在",
            http_status=404,
        )

    if discussion["status"] not in ("done", "terminated", "failed"):
        raise AppException(
            code="TASK_BUSY",
            message=(
                f"讨论状态为 {discussion['status']}，"
                f"仅允许删除 done/terminated/failed 状态的讨论"
            ),
            http_status=409,
        )

    deleted = await asyncio.to_thread(
        discussion_repo.delete_discussion_sync, discussion_id,
    )
    if not deleted:
        raise AppException(
            code="TASK_BUSY",
            message="删除失败，讨论状态可能已变更",
            http_status=409,
        )

    logger.info("讨论已删除：id=%s", discussion_id)
    return ApiResponse(data={"discussion_id": discussion_id, "deleted": True})
