"""共识判定的解析逻辑测试。

_check_consensus 的输出决定讨论是提前结束还是继续烧 token，
解析错了会让「达成了还在跑」或「没达成就收场」。
裁判是外部模型，这里 mock 掉，只测解析与容错。
"""

from __future__ import annotations

import asyncio

from app.services import discussion_engine
from app.services.ai_scheduler import AICallResult

DISCUSSION = {
    "discussion_id": 1,
    "topic": "是否应当限制城市机动车总量",
    "consensus_strictness": "medium",
}


def _patch_judge(monkeypatch, content: str, status: str = "success"):
    async def fake_call_ai(ai_id, messages, **kwargs):
        return AICallResult(ai_id=ai_id, status=status, content=content)

    monkeypatch.setattr(discussion_engine, "call_ai", fake_call_ai)


def _run(monkeypatch, judge_ai="deepseek"):
    return asyncio.run(discussion_engine._check_consensus(
        discussion=DISCUSSION,
        rounds_completed=3,
        round_summaries=["第1轮总结", "第2轮总结"],
        stream_id="test-stream",
        judge_ai=judge_ai,
    ))


def test_json_reached_true(db_seats, monkeypatch):
    _patch_judge(monkeypatch, '{"reached": true, "reason": "核心分歧已回应"}')
    result = _run(monkeypatch)
    assert result["reached"] is True
    assert result["reason"] == "核心分歧已回应"


def test_json_reached_false(db_seats, monkeypatch):
    _patch_judge(monkeypatch, '{"reached": false, "reason": "仍有实质分歧"}')
    assert _run(monkeypatch)["reached"] is False


def test_string_true_is_accepted(db_seats, monkeypatch):
    """模型经常返回字符串 "true" 而不是布尔值。"""
    _patch_judge(monkeypatch, '{"reached": "true", "reason": "已一致"}')
    assert _run(monkeypatch)["reached"] is True


def test_plain_text_fallback(db_seats, monkeypatch):
    """非 JSON 回答走文本兼容路径。"""
    _patch_judge(monkeypatch, "我认为已达成共识，可以收束。")
    assert _run(monkeypatch)["reached"] is True

    _patch_judge(monkeypatch, "目前尚未达成共识。")
    assert _run(monkeypatch)["reached"] is False


def test_judge_failure_defaults_to_continue(db_seats, monkeypatch):
    """裁判调用失败时默认继续讨论，不能因为一次失败就误判结束。"""
    _patch_judge(monkeypatch, "", status="failed")
    result = _run(monkeypatch)
    assert result["reached"] is False
    assert "失败" in result["reason"]
