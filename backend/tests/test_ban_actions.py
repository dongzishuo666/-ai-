"""禁言判定 _compute_ban_actions 的回归测试。

规则回顾（SCORING_THRESHOLD）：
    条件A：连续 ban_consecutive_rounds(=2) 轮末位 且 综合分 < 60
    条件B：本轮合规判定为 violation
    前置：round_no >= 2，且活跃人数 > min_active_participants(=2)

这里钉死的是两个已修复的缺陷：
    P0-4 缺席者被当成末位禁言（离线 AI 0 分）
    P0-3 同一模型多席位时，一个席位违规连带另一个席位
"""

from __future__ import annotations

import asyncio

from app.database import discussion_repo
from app.services import discussion_engine
from helpers import (
    add_participant,
    add_round_score,
    list_participants,
    new_discussion,
)


def _seed_four_seats(discussion_id: int, scores: dict) -> dict:
    """建 4 个席位并返回 {ai_id: seat_id}。scores 给的是初始 total_score。"""
    seats = {}
    for ai_id, (name, total) in scores.items():
        seats[ai_id] = add_participant(
            discussion_id, ai_id, name, total_score=total)
    return seats


def test_consecutive_last_place_and_low_score_is_banned(db_seats):
    """连续两轮末位 + 综合分低于阈值 → 禁言（low_score）。"""
    did = new_discussion()
    seats = _seed_four_seats(did, {
        "deepseek": ("DeepSeek", 20.0),   # 末位且低分
        "zhipu": ("智谱清言", 180.0),
        "kimi": ("Kimi", 160.0),
        "hunyuan": ("腾讯混元", 140.0),
    })

    for r in (1, 2):
        add_round_score(did, r, "deepseek", "DeepSeek", 10.0, seat_id=seats["deepseek"])
        add_round_score(did, r, "zhipu", "智谱清言", 90.0, seat_id=seats["zhipu"])
        add_round_score(did, r, "kimi", "Kimi", 80.0, seat_id=seats["kimi"])
        add_round_score(did, r, "hunyuan", "腾讯混元", 70.0, seat_id=seats["hunyuan"])

    actions = asyncio.run(discussion_engine._compute_ban_actions(
        did, 2, list_participants(did)))

    assert [a["ai_id"] for a in actions] == ["deepseek"], actions
    assert actions[0]["ban_type"] == "low_score"
    assert actions[0]["seat_id"] == seats["deepseek"]


def test_absent_participant_is_not_banned(db_seats):
    """P0-4：第 2 轮缺席的人不被判末位。

    deepseek 第 1 轮高分、第 2 轮缺席。若按「缺席 = 0 分」判定，
    它会被误判末位禁言。真正连续末位的是 hunyuan，它才该被禁。
    """
    did = new_discussion()
    seats = _seed_four_seats(did, {
        "deepseek": ("DeepSeek", 20.0),   # 总分很低，但第2轮缺席
        "zhipu": ("智谱清言", 180.0),
        "kimi": ("Kimi", 160.0),
        "hunyuan": ("腾讯混元", 20.0),    # 两轮都末位且低分
    })

    # 第 1 轮全员
    add_round_score(did, 1, "deepseek", "DeepSeek", 95.0, seat_id=seats["deepseek"])
    add_round_score(did, 1, "zhipu", "智谱清言", 80.0, seat_id=seats["zhipu"])
    add_round_score(did, 1, "kimi", "Kimi", 70.0, seat_id=seats["kimi"])
    add_round_score(did, 1, "hunyuan", "腾讯混元", 10.0, seat_id=seats["hunyuan"])

    # 第 2 轮 deepseek 缺席（没有它的评分记录）
    add_round_score(did, 2, "zhipu", "智谱清言", 80.0, seat_id=seats["zhipu"])
    add_round_score(did, 2, "kimi", "Kimi", 70.0, seat_id=seats["kimi"])
    add_round_score(did, 2, "hunyuan", "腾讯混元", 10.0, seat_id=seats["hunyuan"])

    actions = asyncio.run(discussion_engine._compute_ban_actions(
        did, 2, list_participants(did)))
    banned = {a["ai_id"] for a in actions}

    assert "hunyuan" in banned, "真正连续末位的人应被禁言"
    assert "deepseek" not in banned, "缺席者被误判为末位（P0-4 回归）"


def test_first_round_never_bans(db_seats):
    """第 1 轮只记录不判定。"""
    did = new_discussion()
    seats = _seed_four_seats(did, {
        "deepseek": ("DeepSeek", 0.0),
        "zhipu": ("智谱清言", 100.0),
        "kimi": ("Kimi", 100.0),
        "hunyuan": ("腾讯混元", 100.0),
    })
    add_round_score(did, 1, "deepseek", "DeepSeek", 1.0, seat_id=seats["deepseek"])

    actions = asyncio.run(discussion_engine._compute_ban_actions(
        did, 1, list_participants(did)))
    assert actions == []


def test_min_active_participants_protection(db_seats):
    """活跃人数 <= 2 时不再禁言，避免讨论直接进行不下去。"""
    did = new_discussion()
    # 3 个席位里 2 个已禁言 → 活跃只剩 1 个
    add_participant(did, "deepseek", "DeepSeek", total_score=0.0)
    add_participant(did, "zhipu", "智谱清言", total_score=100.0, is_banned=True)
    add_participant(did, "kimi", "Kimi", total_score=100.0, is_banned=True)

    actions = asyncio.run(discussion_engine._compute_ban_actions(
        did, 3, list_participants(did)))
    assert actions == []


def test_violation_bans_only_that_seat(db_seats, monkeypatch):
    """P0-3：同一模型两个席位，只有一个违规时，只禁违规的那个席位。

    数据库层允许同源多席位，但同一轮写两条评分要靠 mock 绕开唯一约束；
    这里要验的是「按席位判定」这条逻辑本身。
    """
    did = new_discussion()
    seat_a = add_participant(did, "deepseek", "DeepSeek·甲")   # 违规席位
    seat_b = add_participant(did, "deepseek", "DeepSeek·乙")   # 干净席位
    add_participant(did, "zhipu", "智谱清言")
    add_participant(did, "kimi", "Kimi")
    participants = list_participants(did)

    # 本轮四个席位都有评分（甲、乙同源，靠 seat_id 区分）
    monkeypatch.setattr(
        discussion_repo, "get_round_scores_sync",
        lambda d, r: [
            {"ai_id": "deepseek", "display_name": "DeepSeek·甲", "seat_id": seat_a},
            {"ai_id": "deepseek", "display_name": "DeepSeek·乙", "seat_id": seat_b},
            {"ai_id": "zhipu", "display_name": "智谱清言", "seat_id": 3},
            {"ai_id": "kimi", "display_name": "Kimi", "seat_id": 4},
        ],
    )
    # 只有甲违规
    monkeypatch.setattr(
        discussion_engine, "_has_violation_in_round_sync",
        lambda d, r, ai_id, display_name=None: display_name == "DeepSeek·甲",
    )
    # 都不是末位，排除低分分支干扰
    monkeypatch.setattr(
        discussion_repo, "compute_rank_in_round_sync",
        lambda d, r, ai_id, seat_id=None: (1, 4),
    )

    actions = asyncio.run(discussion_engine._compute_ban_actions(did, 2, participants))

    assert len(actions) == 1, f"应只禁一个席位，实际: {actions}"
    assert actions[0]["display_name"] == "DeepSeek·甲"
    assert actions[0]["seat_id"] == seat_a
    assert actions[0]["ban_type"] == "violation"
