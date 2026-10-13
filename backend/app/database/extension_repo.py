# ============================================================
# 文件：backend/app/database/extension_repo.py
# 说明：扩展功能数据访问层。
#       模块A即时对话 / 模块B弹劾 / 模块E认知框架 / 模块F信用分
# 约束：同步 sqlite3 + 手动事务（与 connection.py 一致）
# ============================================================

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.database.connection import get_connection, get_transaction


# ============================================================
# 模块A：即时对话 chat_sessions / chat_messages
# ============================================================

def ensure_chat_session_columns_sync() -> None:
    """
    补建 chat_sessions 的新增列（ai_id）。

    init.sql 只在首次建库时执行，已存在的库靠这里补齐，避免手改线上 DB。
    列已存在时 PRAGMA 能查到，直接跳过。
    """
    with get_transaction() as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(chat_sessions)").fetchall()}
        if "ai_id" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN ai_id TEXT")


def create_chat_session_sync(title: str, mode: str,
                             supervisor_enabled: bool, memory_enabled: bool,
                             ai_id: Optional[str] = None) -> int:
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO chat_sessions (title, mode, supervisor_enabled, memory_enabled, ai_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, mode, int(supervisor_enabled), int(memory_enabled), ai_id),
        )
        return cur.lastrowid


def update_chat_session_ai_sync(session_id: int, ai_id: Optional[str]) -> bool:
    """切换会话使用的模型（传 None 表示回到按模式自动挑选）。"""
    with get_transaction() as conn:
        cur = conn.execute(
            "UPDATE chat_sessions SET ai_id = ? WHERE id = ?", (ai_id, session_id))
        return cur.rowcount > 0


def get_chat_session_sync(session_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def list_chat_sessions_sync(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_chat_session_sync(session_id: int) -> bool:
    with get_transaction() as conn:
        cur = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def insert_chat_message_sync(session_id: int, role: str, content: str,
                             ai_id: Optional[str] = None,
                             is_anomaly: bool = False,
                             anomaly_reason: Optional[str] = None,
                             rewrite_of: Optional[int] = None) -> int:
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO chat_messages
                (session_id, role, ai_id, content, is_anomaly, anomaly_reason, rewrite_of)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, ai_id, content, int(is_anomaly), anomaly_reason, rewrite_of),
        )
        return cur.lastrowid


def mark_chat_message_anomaly_sync(message_id: int, reason: str) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE chat_messages SET is_anomaly = 1, anomaly_reason = ? WHERE id = ?",
            (reason, message_id),
        )


def list_chat_messages_sync(session_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM chat_messages WHERE session_id = ?
            ORDER BY id ASC LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 模块B/F：弹劾 discussion_impeachments
# ============================================================

def insert_impeachment_sync(discussion_id: int, round_no: int,
                            initiator_ai_id: str, target_role: str,
                            target_ai_id: str, reason: str) -> int:
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO discussion_impeachments
                (discussion_id, round, initiator_ai_id, target_judge_role,
                 target_ai_id, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (discussion_id, round_no, initiator_ai_id, target_role, target_ai_id, reason),
        )
        return cur.lastrowid


def finish_impeachment_sync(impeachment_id: int, votes: Dict[str, str],
                            agree: int, disagree: int, result: str,
                            replacement_ai_id: Optional[str]) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE discussion_impeachments
            SET votes_json = ?, agree_count = ?, disagree_count = ?,
                result = ?, replacement_ai_id = ?
            WHERE id = ?
            """,
            (json.dumps(votes, ensure_ascii=False), agree, disagree,
             result, replacement_ai_id, impeachment_id),
        )


def has_pending_impeachment_sync(discussion_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM discussion_impeachments
            WHERE discussion_id = ? AND result = 'pending'
            """,
            (discussion_id,),
        ).fetchone()
    return bool(row and row["c"] > 0)


def list_impeachments_sync(discussion_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM discussion_impeachments
            WHERE discussion_id = ? ORDER BY id ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 模块E：认知框架 cognitive_frames
# ============================================================

def insert_frame_sync(frame_text: str, topic: str = "",
                      source_discussion_id: Optional[int] = None,
                      generation: int = 1) -> int:
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO cognitive_frames
                (source_discussion_id, topic, frame_text, generation)
            VALUES (?, ?, ?, ?)
            """,
            (source_discussion_id, topic, frame_text, generation),
        )
        return cur.lastrowid


def get_active_frame_sync() -> Optional[Dict[str, Any]]:
    """取最新一代 active 框架（讨论时注入认知连续性）。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM cognitive_frames WHERE status = 'active'
            ORDER BY generation DESC, id DESC LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def retire_frame_sync(frame_id: int, status: str = "retired") -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE cognitive_frames SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (status, frame_id),
        )


def list_frames_sync(limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cognitive_frames ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 模块F：用户信用分 user_credit / user_behavior_logs
# ============================================================

def get_credit_sync(user_id: str = "default") -> Dict[str, Any]:
    with get_transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_credit (user_id) VALUES (?)", (user_id,)
        )
        row = conn.execute(
            "SELECT * FROM user_credit WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row)


def apply_credit_sync(user_id: str, delta: float,
                      restrictions: Optional[List[str]] = None) -> Dict[str, Any]:
    with get_transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_credit (user_id) VALUES (?)", (user_id,)
        )
        conn.execute(
            """
            UPDATE user_credit
            SET credit_score = MAX(0, MIN(100, credit_score + ?)),
                restrictions_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE user_id = ?
            """,
            (delta,
             json.dumps(restrictions or [], ensure_ascii=False),
             user_id),
        )
        row = conn.execute(
            "SELECT * FROM user_credit WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row)


def log_user_action_sync(user_id: str, action: str, detail: str,
                         credit_delta: float = 0.0) -> int:
    counters = {
        "thumbs_down": "total_thumbs_down = total_thumbs_down + 1",
        "malicious_report": "total_malicious_reports = total_malicious_reports + 1",
    }
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO user_behavior_logs (user_id, action, detail, credit_delta)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, action, detail, credit_delta),
        )
        if action in counters:
            conn.execute(
                "INSERT OR IGNORE INTO user_credit (user_id) VALUES (?)",
                (user_id,),
            )
            conn.execute(
                f"UPDATE user_credit SET {counters[action]}, "
                "credit_score = MAX(0, MIN(100, credit_score + ?)) "
                "WHERE user_id = ?",
                (credit_delta, user_id),
            )
        return cur.lastrowid


def list_recent_behavior_sync(user_id: str = "default", limit: int = 10) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM user_behavior_logs WHERE user_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
