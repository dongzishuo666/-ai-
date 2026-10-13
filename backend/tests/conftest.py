"""测试公共配置。

核心：把数据库指向临时文件，绝不碰 backend/data/app.db。

为什么 monkeypatch 一个点就够：
    app/database/connection.py 里 DATABASE_PATH 是模块级名字，
    _new_connection() 每次都重新读它、且每次新建连接（没有连接池缓存），
    所以不存在「第一个用例连到真实库」这类坑。

fixture 分两层：
    db        —— 只建表（init.sql）
    db_seats  —— 建表 + 跑生产迁移 ensure_multi_seat_schema_sync()，
                 得到与线上一致的结构（round_scores 含 seat_id、
                 唯一约束按席位、放开同一模型多席位）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让 `import app.xxx` 可用（rootdir 是 backend/）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import connection, discussion_repo  # noqa: E402


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    """临时库 + 建表。"""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(connection, "DATABASE_PATH", db_file)
    connection.init_database()
    return db_file


@pytest.fixture
def db_seats(db: Path) -> Path:
    """临时库 + 建表 + 多席位迁移（等价于线上表结构）。"""
    discussion_repo.ensure_multi_seat_schema_sync()
    return db
