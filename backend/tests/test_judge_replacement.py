"""弹劾通过后裁判替换的回归测试。

关键点：换人必须落到 discussion_participants 这张表（单一数据源），
而不是只写一条弹劾记录——引擎取裁判一律走 get_judge_sync，
两套数据源并存会出现「记录里说换了、引擎还在用旧裁判」。
"""

from __future__ import annotations

from app.database import discussion_repo
from helpers import add_participant, new_discussion


def test_judge_is_replaced_after_impeachment(db_seats):
    """弹劾通过后，get_judge_sync 返回的是替补，不是原裁判。"""
    did = new_discussion()
    add_participant(did, "deepseek", "秩序裁判·DeepSeek",
                    role="judge", judge_type="order")

    before = discussion_repo.get_judge_sync(did, "order")
    assert before is not None and before["ai_id"] == "deepseek"

    assert discussion_repo.update_judge_sync(did, "order", "kimi") is True

    after = discussion_repo.get_judge_sync(did, "order")
    assert after["ai_id"] == "kimi"
    # 席位名要跟着换，界面上能看出换成了谁
    assert after["display_name"] != before["display_name"]
    assert "Kimi" in after["display_name"]


def test_only_target_role_is_replaced(db_seats):
    """换 order 裁判不应影响 evaluation 裁判。"""
    did = new_discussion()
    add_participant(did, "deepseek", "秩序裁判", role="judge", judge_type="order")
    add_participant(did, "zhipu", "评价裁判", role="judge", judge_type="evaluation")

    discussion_repo.update_judge_sync(did, "order", "kimi")

    assert discussion_repo.get_judge_sync(did, "order")["ai_id"] == "kimi"
    assert discussion_repo.get_judge_sync(did, "evaluation")["ai_id"] == "zhipu"


def test_unknown_model_is_rejected(db_seats):
    """替补模型不在注册表里 → 拒绝替换，原裁判保持不变。"""
    did = new_discussion()
    add_participant(did, "deepseek", "秩序裁判", role="judge", judge_type="order")

    assert discussion_repo.update_judge_sync(did, "order", "不存在的模型") is False
    assert discussion_repo.get_judge_sync(did, "order")["ai_id"] == "deepseek"
