"""SQLite 数据访问层。

原则（对应技术方案）：
- 开启 WAL 模式提升并发读写性能；
- 单 uvicorn worker 下写操作天然安全，用线程锁进一步保证同步；
- 所有写入通过 ``run_db`` 丢到线程池，避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from app.config import settings

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    need TEXT NOT NULL,
    doc_name TEXT DEFAULT '',
    doc_type TEXT DEFAULT '',
    result TEXT NOT NULL,
    engine TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discussions (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    participants TEXT NOT NULL,
    judge TEXT DEFAULT '',
    result TEXT DEFAULT '',
    report TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id TEXT NOT NULL,
    round_no INTEGER NOT NULL,
    ai_id TEXT NOT NULL,
    ai_name TEXT NOT NULL,
    role TEXT DEFAULT 'member',
    content TEXT NOT NULL,
    score REAL DEFAULT 0,
    anomaly INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scoreboard (
    discussion_id TEXT NOT NULL,
    ai_id TEXT NOT NULL,
    ai_name TEXT NOT NULL,
    score REAL DEFAULT 0,
    speak_count INTEGER DEFAULT 0,
    contributions REAL DEFAULT 0,
    anomalies INTEGER DEFAULT 0,
    last_rank INTEGER DEFAULT 0,
    low_streak INTEGER DEFAULT 0,
    banned_until INTEGER DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (discussion_id, ai_id)
);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    genre TEXT DEFAULT '',
    style TEXT DEFAULT '',
    length TEXT DEFAULT '',
    requirement TEXT DEFAULT '',
    outline TEXT DEFAULT '',
    draft TEXT DEFAULT '',
    final_text TEXT DEFAULT '',
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_discussion ON messages(discussion_id);
CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at);
"""


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def init_db() -> None:
    """应用启动时初始化数据库。"""
    with _lock:
        _connect()


def _execute_sync(sql: str, params: Iterable[Any] = ()) -> int:
    with _lock:
        conn = _connect()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid or 0


def _query_sync(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        conn = _connect()
        cur = conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


async def execute(sql: str, params: Iterable[Any] = ()) -> int:
    return await asyncio.to_thread(_execute_sync, sql, params)


async def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return await asyncio.to_thread(_query_sync, sql, params)


async def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = await query(sql, params)
    return rows[0] if rows else None


def now() -> float:
    return time.time()


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def load(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default
