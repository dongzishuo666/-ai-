"""测试数据构造辅助。

所有写操作都走 app.database.connection，因此会落到 conftest 切好的临时库。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.database.connection import get_transaction


def new_discussion(topic: str = "测试议题", **kwargs: Any) -> int:
    """插入一条讨论，返回 id。"""
    fields = {"topic": topic, **kwargs}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with get_transaction() as conn:
        cur = conn.execute(
            f"INSERT INTO discussions ({cols}) VALUES ({marks})",
            tuple(fields.values()),
        )
        return int(cur.lastrowid)


def add_participant(
    discussion_id: int,
    ai_id: str,
    display_name: str,
    *,
    role: str = "discussant",
    judge_type: Optional[str] = None,
    channel: str = "official",
    total_score: float = 0.0,
    is_banned: bool = False,
) -> int:
    """插入一个席位，返回席位 id（= seat_id）。"""
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO discussion_participants
                (discussion_id, ai_id, display_name, channel, role,
                 judge_type, is_banned, total_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (discussion_id, ai_id, display_name, channel, role,
             judge_type, int(is_banned), total_score),
        )
        return int(cur.lastrowid)


def add_round_score(
    discussion_id: int,
    round_no: int,
    ai_id: str,
    display_name: str,
    weighted_total: float,
    *,
    seat_id: Optional[int] = None,
    judge_model: str = "test-judge",
) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            INSERT INTO round_scores
                (discussion_id, round, ai_id, seat_id, display_name,
                 weighted_total, judge_model)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (discussion_id, round_no, ai_id, seat_id, display_name,
             weighted_total, judge_model),
        )


def add_message(
    discussion_id: int,
    round_no: int,
    ai_id: str,
    display_name: str,
    *,
    content: str = "测试发言内容",
    compliance_status: str = "passed",
    message_order: int = 1,
) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            INSERT INTO discussion_messages
                (discussion_id, round, message_order, ai_id, display_name,
                 content, compliance_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (discussion_id, round_no, message_order, ai_id, display_name,
             content, compliance_status),
        )


def list_participants(discussion_id: int, role: str = "discussant") -> List[Dict[str, Any]]:
    """按 _compute_ban_actions 需要的字段读回参与者。"""
    from app.database.connection import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, ai_id, display_name, is_banned, total_score
            FROM discussion_participants
            WHERE discussion_id = ? AND role = ?
            ORDER BY id
            """,
            (discussion_id, role),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ai_id": r["ai_id"],
            "display_name": r["display_name"],
            "is_banned": bool(r["is_banned"]),
            "total_score": r["total_score"],
        }
        for r in rows
    ]
