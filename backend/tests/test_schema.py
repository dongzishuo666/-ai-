"""建表脚本与迁移的回归测试。

防的是这次补测试时实际踩到的两个坑：

1. init.sql 末尾混进了 DOS 的 EOF 控制字符 \\x1a（0x1A）。
   executescript 遇到它直接失败，新环境连建库都做不到。
   老库因为已存在、启动时根本不执行建表，所以这个问题一直没暴露。

2. round_scores.seat_id 和「按席位唯一」是 ensure_multi_seat_schema_sync()
   迁移出来的，不是建表脚本自带的。启动流程只要漏掉这一步，
   就会退化成 UNIQUE(discussion_id, round, ai_id)——同一模型的多席位
   评分互相覆盖，P0-3 修好的多席位判定跟着失效。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.database import connection, discussion_repo

INIT_SQL = Path(__file__).resolve().parent.parent / "app" / "database" / "init.sql"


def test_init_sql_has_no_stray_control_chars():
    """init.sql 不能含 \\x1a 之类的控制字符（会打断 executescript）。"""
    raw = INIT_SQL.read_bytes()
    illegal = [b for b in raw if b < 0x20 and b not in (0x09, 0x0A, 0x0D)]
    assert not illegal, f"init.sql 含非法控制字符: {[hex(b) for b in illegal]}"


def test_init_database_creates_core_tables(db):
    """init_database() 能完整执行，核心表都在。"""
    with connection.get_connection() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    required = {
        "discussions", "discussion_participants", "discussion_messages",
        "round_scores", "discussion_bans", "ai_call_logs",
    }
    assert required <= tables, f"缺表: {required - tables}"


def test_multi_seat_migration_adds_seat_id(db_seats):
    """迁移后 round_scores 必须有 seat_id，否则评分查询报 no such column。"""
    with connection.get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(round_scores)")]
    assert "seat_id" in cols


def test_multi_seat_migration_makes_unique_by_seat(db_seats):
    """唯一约束必须按席位，否则同源多席位的评分互相覆盖。"""
    with connection.get_connection() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='round_scores'"
        ).fetchone()
    sql = re.sub(r"\s+", " ", (row[0] or "")).upper()
    assert "UNIQUE(DISCUSSION_ID, ROUND, SEAT_ID)" in sql, \
        f"唯一约束仍按 ai_id，多席位评分会被覆盖: {sql}"


def test_multi_seat_migration_allows_same_model_two_seats(db_seats):
    """同一模型占两个席位能入库（idx_dp_unique 已放开）。"""
    from helpers import add_participant, new_discussion

    did = new_discussion()
    add_participant(did, "deepseek", "DeepSeek·甲")
    add_participant(did, "deepseek", "DeepSeek·乙")  # 不应抛 UNIQUE 约束错误

    assert discussion_repo.get_participant_count_sync(did) == 2


def test_round_scores_query_with_seat_id_runs(db_seats):
    """get_round_scores_sync 会 SELECT seat_id，跑通即证明列存在。"""
    from helpers import add_round_score, new_discussion

    did = new_discussion()
    add_round_score(did, 1, "deepseek", "DeepSeek", 80.0, seat_id=1)
    rows = discussion_repo.get_round_scores_sync(did, 1)
    assert len(rows) == 1
    assert rows[0]["ai_id"] == "deepseek"
