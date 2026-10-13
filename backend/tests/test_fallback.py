"""降级链的回归测试。

要钉死的是：降级成功时，返回的必须是「实际服务的模型」，
并且把原始主模型记在 fallback_from 上。
否则调用日志里看到的是主模型的名字，排查时会被误导。
"""

from __future__ import annotations

import asyncio

from app.services import ai_scheduler
from app.services.ai_scheduler import AICallResult


def _patch_retry(monkeypatch, behaviour):
    """替换真实的重试/网络调用，按 ai_id 决定成败。"""

    async def fake_retry(ai_id, messages, **kwargs):
        return behaviour(ai_id)

    monkeypatch.setattr(ai_scheduler, "_try_call_with_retry", fake_retry)
    # 别让失败计数污染全局熔断器（会影响同进程里的其他用例）
    monkeypatch.setattr(ai_scheduler, "_record_failure", lambda *a, **k: None)


def test_fallback_records_actual_model(db_seats, monkeypatch):
    """主模型失败 → 走降级链 → 返回备用模型并标记来源。"""
    _patch_retry(monkeypatch, lambda ai_id: (
        AICallResult(ai_id=ai_id, status="failed", error_message="模拟主模型失败")
        if ai_id == "claude"
        else AICallResult(ai_id=ai_id, status="success", content="备用模型回答")
    ))

    result = asyncio.run(ai_scheduler.call_ai(
        "claude", [{"role": "user", "content": "你好"}],
        task_type="discussion", fallback=["deepseek"],
    ))

    assert result.status == "success"
    assert result.ai_id == "deepseek", "应回写实际服务的模型"
    assert result.fallback_used is True
    assert result.fallback_from == "claude"
    assert result.content == "备用模型回答"


def test_primary_success_marks_no_fallback(db_seats, monkeypatch):
    """主模型成功时不该留下降级标记。"""
    _patch_retry(monkeypatch, lambda ai_id: AICallResult(
        ai_id=ai_id, status="success", content="主模型回答"))

    result = asyncio.run(ai_scheduler.call_ai(
        "claude", [{"role": "user", "content": "你好"}],
        task_type="discussion", fallback=["deepseek"],
    ))

    assert result.ai_id == "claude"
    assert result.fallback_used is False
    assert result.fallback_from is None


def test_fallback_skips_the_primary_itself(db_seats, monkeypatch):
    """降级链里若包含主模型自身，应跳过（否则等于原地重试）。"""
    called = []
    _patch_retry(monkeypatch, lambda ai_id: (
        called.append(ai_id) or AICallResult(
            ai_id=ai_id, status="failed", error_message="全挂")
    ))

    result = asyncio.run(ai_scheduler.call_ai(
        "claude", [{"role": "user", "content": "你好"}],
        task_type="discussion", fallback=["claude", "deepseek"],
    ))

    assert called.count("claude") == 1, f"主模型被重复调用: {called}"
    assert result.status == "skipped", "全链失败且 AI_SKIP_ON_FAILURE 时应为 skipped"
