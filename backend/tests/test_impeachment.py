"""弹劾投票阈值的回归测试。

规则：发起者默认投同意，其余未禁言讨论者各投一票，
      passed = agree > total_voters / 2（严格过半，等于一半不算通过）。
"""

from __future__ import annotations

import asyncio

from app.database import discussion_repo
from app.services import extension_service
from app.services.ai_scheduler import AICallResult
from helpers import add_participant, new_discussion


def _patch(monkeypatch, votes: dict):
    """votes: {ai_id: 回答文本}，未列出的默认投反对。

    同时 mock 掉模型探活：_pick_replacement_judge 会调 probe_all_models()
    做真实网络请求，测试里必须换成固定结果，否则候选全 offline 选不出替补。
    """

    async def fake_call_ai(ai_id, messages, **kwargs):
        return AICallResult(
            ai_id=ai_id, status="success",
            content=votes.get(ai_id, "/disagree"),
        )

    async def fake_publish(stream_id, event_type, data=None, **kwargs):
        return None

    async def fake_probe():
        return {"models": [{"ai_id": "wenxin", "status": "online"}]}

    monkeypatch.setattr(extension_service, "call_ai", fake_call_ai)
    monkeypatch.setattr(extension_service.sse_manager, "publish", fake_publish)
    monkeypatch.setattr(extension_service, "probe_all_models", fake_probe)


def _setup_with_three_discussants() -> int:
    did = new_discussion()
    add_participant(did, "deepseek", "秩序裁判", role="judge", judge_type="order")
    add_participant(did, "zhipu", "智谱清言")
    add_participant(did, "kimi", "Kimi")
    add_participant(did, "hunyuan", "腾讯混元")
    return did


def _run(did: int, initiator: str = "zhipu") -> dict:
    return asyncio.run(extension_service.run_impeachment(
        discussion_id=did,
        initiator_ai_id=initiator,
        target_role="order",
        reason="裁判打分明显偏袒",
        stream_id="test-stream",
    ))


def test_passed_when_majority_agree(db_seats, monkeypatch):
    """3 人投票：发起者 + 1 人同意 = 2/3，过半通过。"""
    did = _setup_with_three_discussants()
    _patch(monkeypatch, {"kimi": "/agree", "hunyuan": "/disagree"})

    event = _run(did)

    assert event["result"] == "passed"
    assert event["agree_count"] == 2
    assert event["replacement_ai_id"], "通过后必须给出替补裁判"


def test_rejected_when_only_initiator_agrees(db_seats, monkeypatch):
    """只有发起者同意（1/3），不通过。"""
    did = _setup_with_three_discussants()
    _patch(monkeypatch, {"kimi": "/disagree", "hunyuan": "/disagree"})

    event = _run(did)

    assert event["result"] == "rejected"
    assert event["agree_count"] == 1
    # 驳回时不能动裁判
    assert discussion_repo.get_judge_sync(did, "order")["ai_id"] == "deepseek"


def test_half_is_not_enough(db_seats, monkeypatch):
    """2 人投票时 1 票恰好一半，严格过半才算通过 → 驳回。"""
    did = new_discussion()
    add_participant(did, "deepseek", "秩序裁判", role="judge", judge_type="order")
    add_participant(did, "zhipu", "智谱清言")
    add_participant(did, "kimi", "Kimi")
    _patch(monkeypatch, {"kimi": "/disagree"})

    event = _run(did, initiator="zhipu")

    assert event["agree_count"] == 1
    assert event["result"] == "rejected", "恰好一半不应通过"


def test_passed_replaces_the_judge(db_seats, monkeypatch):
    """通过后裁判要真的换掉，不能只留一条弹劾记录。"""
    did = _setup_with_three_discussants()
    _patch(monkeypatch, {"kimi": "/agree", "hunyuan": "/agree"})

    event = _run(did)

    assert event["result"] == "passed"
    assert event.get("replaced") is True
    assert discussion_repo.get_judge_sync(did, "order")["ai_id"] != "deepseek"
