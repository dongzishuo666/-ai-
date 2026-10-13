# ============================================================
# 文件：backend/app/database/memory_repo.py
# 说明：记忆库数据访问层（memories 表）
#
#   记忆来源：
#     judge      —— 裁判总结（最终评价裁判产出，可视化四节结构）
#     discussion —— 讨论者 13 部分总结
#     frame      —— 认知框架演化
#     manual     —— 手动写入
#
#   重要：init.sql 仅在「首次建库」时执行，已经存在的 app.db 不会补建新表。
#        因此本模块自带 ensure_table_sync()，由 main.py 的 lifespan 调用，
#        保证老库升级后同样具备 memories 表与索引。
# ============================================================

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.database.connection import get_connection, get_transaction

logger = logging.getLogger("app.database.memory_repo")


# 建表语句（幂等，可反复执行）
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type   TEXT NOT NULL CHECK(memory_type IN ('judge', 'discussion', 'frame', 'manual')),
    topic         TEXT,
    content       TEXT NOT NULL,
    sections_json TEXT,
    source_type   TEXT,
    source_id     INTEGER,
    judge_model   TEXT,
    ai_id         TEXT,
    display_name  TEXT,
    char_count    INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_type    ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mem_source  ON memories(source_type, source_id);
"""


def ensure_table_sync() -> None:
    """确保 memories 表与索引存在（老库升级用，幂等）。"""
    with get_connection() as conn:
        conn.executescript(_CREATE_TABLE_SQL)


def _row_to_dict(row) -> Dict[str, Any]:
    """sqlite3.Row → dict，并把 sections_json 解析为对象返回给前端。"""
    item = dict(row)
    raw = item.get("sections_json")
    if raw:
        try:
            item["sections"] = json.loads(raw)
        except (ValueError, TypeError):
            item["sections"] = None
    else:
        item["sections"] = None
    item.pop("sections_json", None)
    return item


def insert_memory_sync(
    memory_type: str,
    content: str,
    topic: Optional[str] = None,
    sections: Optional[Dict[str, Any]] = None,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
    judge_model: Optional[str] = None,
    ai_id: Optional[str] = None,
    display_name: Optional[str] = None,
) -> int:
    """写入一条记忆，返回新记录 id。"""
    sections_json = json.dumps(sections, ensure_ascii=False) if sections else None
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO memories (
                memory_type, topic, content, sections_json,
                source_type, source_id, judge_model, ai_id, display_name, char_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_type,
                (topic or "")[:300] or None,
                content,
                sections_json,
                source_type,
                source_id,
                judge_model,
                ai_id,
                display_name,
                len(content or ""),
            ),
        )
        return int(cur.lastrowid)


def list_memories_sync(
    keyword: Optional[str] = None,
    memory_type: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    记忆检索：关键词 + 类型 + 来源过滤，按时间倒序分页。

    keyword 同时匹配正文 / 主题 / 发言者名，满足「真正实现记忆检索」的诉求。
    """
    where: List[str] = []
    params: List[Any] = []

    if keyword:
        like = f"%{keyword}%"
        where.append("(content LIKE ? OR topic LIKE ? OR display_name LIKE ? OR judge_model LIKE ?)")
        params.extend([like, like, like, like])
    if memory_type:
        where.append("memory_type = ?")
        params.append(memory_type)
    if source_type:
        where.append("source_type = ?")
        params.append(source_type)
    if source_id is not None:
        where.append("source_id = ?")
        params.append(source_id)

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM memories{where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"SELECT * FROM memories{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, int(limit), int(offset)],
        ).fetchall()

    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
    }


def get_memory_sync(memory_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_memory_sync(memory_id: int) -> bool:
    with get_transaction() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def delete_by_source_sync(
    source_type: str,
    source_id: int,
    memory_type: Optional[str] = None,
) -> int:
    """
    删除同一来源的记忆（用于「一场讨论只保留最新一份总结」）。

    重复点击「裁判总结」不应在记忆库里堆出多条几乎一样的记录，
    因此写入前先清掉该来源的同类型旧记忆。
    """
    with get_transaction() as conn:
        if memory_type:
            cur = conn.execute(
                "DELETE FROM memories WHERE source_type = ? AND source_id = ? AND memory_type = ?",
                (source_type, source_id, memory_type),
            )
        else:
            cur = conn.execute(
                "DELETE FROM memories WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            )
        return cur.rowcount


def clear_memories_sync(memory_type: Optional[str] = None) -> int:
    """清空记忆（不传类型则全清）。"""
    with get_transaction() as conn:
        if memory_type:
            cur = conn.execute(
                "DELETE FROM memories WHERE memory_type = ?", (memory_type,))
        else:
            cur = conn.execute("DELETE FROM memories")
        return cur.rowcount


def stats_sync() -> Dict[str, Any]:
    """记忆库概览统计，供页面顶部卡片使用。"""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]

        type_rows = conn.execute(
            "SELECT memory_type, COUNT(*) AS c FROM memories GROUP BY memory_type"
        ).fetchall()

        recent = conn.execute(
            """
            SELECT COUNT(*) AS c FROM memories
             WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')
            """
        ).fetchone()["c"]

        chars = conn.execute(
            "SELECT COALESCE(SUM(char_count), 0) AS c FROM memories"
        ).fetchone()["c"]

        topics = conn.execute(
            """
            SELECT COUNT(DISTINCT topic) AS c FROM memories
             WHERE topic IS NOT NULL AND topic <> ''
            """
        ).fetchone()["c"]

        latest = conn.execute(
            "SELECT MAX(created_at) AS t FROM memories"
        ).fetchone()["t"]

    return {
        "total": int(total),
        "by_type": {r["memory_type"]: int(r["c"]) for r in type_rows},
        "recent_7d": int(recent),
        "total_chars": int(chars),
        "topic_count": int(topics),
        "latest_at": latest,
    }
