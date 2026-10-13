# ============================================================
# 文件：backend/app/database/connection.py
# 版本：v2（新增 get_db() 别名）
# 说明：SQLite 连接管理。提供 get_connection()/get_db()（只读）与
#       get_transaction()（写事务）上下文管理器；
#       首次启动时自动执行 init.sql 建表。
# 技术说明：sqlite3 是同步库，本模块所有函数均为同步；
#           业务侧调用必须用 asyncio.to_thread 包装（已在前序批次统一）。
# ============================================================

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from app.config import DATABASE_PATH


# init.sql 与本文件同目录
INIT_SQL_PATH: Path = Path(__file__).resolve().parent / "init.sql"


# ============================================================
# 内部：连接工厂与 PRAGMA 配置
# ============================================================

def _configure(conn: sqlite3.Connection) -> None:
    """
    对单个连接应用 PRAGMA（per-connection 项）。
    说明：
      - foreign_keys / busy_timeout / cache_size 是 per-connection，
        每次新连接都必须设置（init.sql 只在首次建库时执行一次）
      - journal_mode / synchronous 是持久化设置，由 init.sql 或
        init_database() 设置一次即可
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA cache_size = -4000")
    conn.execute("PRAGMA synchronous = NORMAL")


def _new_connection() -> sqlite3.Connection:
    """创建一个已配置好的 SQLite 连接（autocommit 模式，手动事务）。"""
    conn = sqlite3.connect(
        str(DATABASE_PATH),
        timeout=5.0,
        isolation_level=None,      # 关闭 python sqlite3 自动事务，改手动 BEGIN/COMMIT
        check_same_thread=False,   # 允许跨线程（配合 asyncio.to_thread）
    )
    conn.row_factory = sqlite3.Row
    _configure(conn)
    return conn


# ============================================================
# 对外：连接上下文管理器
# ============================================================

@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    获取只读连接（autocommit，适合 SELECT）。
    用完后自动关闭。

    用法：
        with get_connection() as conn:
            rows = conn.execute("SELECT ...").fetchall()
    """
    conn = _new_connection()
    try:
        yield conn
    finally:
        conn.close()


# 别名：满足 get_db() 命名习惯（等同 get_connection）
get_db = get_connection


@contextmanager
def get_transaction() -> Generator[sqlite3.Connection, None, None]:
    """
    获取写事务连接（显式 BEGIN / COMMIT / ROLLBACK）。
    出现异常时自动回滚；正常退出时提交。

    用法：
        with get_transaction() as conn:
            conn.execute("UPDATE ...")
            conn.execute("INSERT ...")
        # 此处已 COMMIT
    """
    conn = _new_connection()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # 已无活跃事务，忽略
        raise
    finally:
        conn.close()


# ============================================================
# 数据库初始化
# ============================================================

def _is_database_ready() -> bool:
    """判断 app.db 是否已存在且非空。"""
    return DATABASE_PATH.exists() and DATABASE_PATH.stat().st_size > 0


def init_database() -> bool:
    """
    应用启动时调用。
    - app.db 不存在或为空 → 执行 init.sql 建表 + 初始数据
    - app.db 已存在       → 仅确保 journal_mode = WAL

    返回：
        True  → 本次执行了建表（首次启动）
        False → 数据库已存在，未建表
    """
    is_new = not _is_database_ready()

    conn = _new_connection()
    try:
        # WAL 是持久化设置，幂等执行（保障已存在库也处于 WAL）
        conn.execute("PRAGMA journal_mode = WAL")

        if is_new:
            sql = INIT_SQL_PATH.read_text(encoding="utf-8")
            conn.executescript(sql)
    finally:
        conn.close()

    return is_new
