# ============================================================
# 文件：backend/app/database/service_run_repo.py
# 说明：服务运行日记 —— 累计运行时长统计。
#
# 背景：/health 里的 uptime_seconds 是「当前进程」的运行时长，
#   每次部署重启就归零，看不出平台服役多久。这里把每次启动记成一段
#   （boot_at → shutdown_at），累计相加才是真正的总运行时长。
#
# 设计：
#   - 启动时插一行（last_heartbeat_at = boot_at），关闭时补 shutdown_at；
#   - 崩溃 / kill -9 时来不及写 shutdown_at，靠心跳 last_heartbeat_at 截断，
#     最多多算一个心跳周期（30s），不会把「已下线的时间」算进在线时长；
#   - 历史段（本次上线之前）由 journal 回填，source='journal'，与应用记录口径一致。
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database.connection import get_connection, get_transaction

_TABLE = "service_runs"


def _utc_now_iso() -> str:
    """与库内其它表保持一致：'YYYY-MM-DDTHH:MM:SSZ'（UTC）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    """解析库内时间串。SQLite 的日期函数对 'Z' 后缀支持不稳，这里自己解析。"""
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ============================================================
# 建表
# ============================================================

def ensure_table_sync() -> None:
    """补建 service_runs（init.sql 只在首次建库执行，老库靠这里补齐）。"""
    with get_transaction() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                boot_at           TEXT NOT NULL,
                last_heartbeat_at TEXT NOT NULL,
                shutdown_at       TEXT,
                -- app: 应用自己记的；journal: 由 systemd 日志回填的历史段
                source            TEXT NOT NULL DEFAULT 'app',
                created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_sr_boot ON {_TABLE}(boot_at)")


# ============================================================
# 写入
# ============================================================

def start_run_sync() -> int:
    """记录一次启动，返回 run_id。"""
    now = _utc_now_iso()
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            INSERT INTO {_TABLE} (boot_at, last_heartbeat_at, source)
            VALUES (?, ?, 'app')
            """,
            (now, now),
        )
        return int(cur.lastrowid)


def heartbeat_sync(run_id: int) -> None:
    """心跳：证明这段还在跑。崩溃时用它截断，避免虚增在线时长。"""
    with get_transaction() as conn:
        conn.execute(
            f"UPDATE {_TABLE} SET last_heartbeat_at = ? WHERE id = ?",
            (_utc_now_iso(), run_id),
        )


def end_run_sync(run_id: int) -> None:
    """正常关闭：补上 shutdown_at。"""
    with get_transaction() as conn:
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET shutdown_at = ?, last_heartbeat_at = ?
            WHERE id = ? AND shutdown_at IS NULL
            """,
            (_utc_now_iso(), _utc_now_iso(), run_id),
        )


def insert_segment_sync(boot_at: str, shutdown_at: str,
                        source: str = "journal") -> int:
    """写入一段已结束的运行记录（历史回填用）。"""
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            INSERT INTO {_TABLE}
                (boot_at, last_heartbeat_at, shutdown_at, source)
            VALUES (?, ?, ?, ?)
            """,
            (boot_at, shutdown_at, shutdown_at, source),
        )
        return int(cur.lastrowid)


def count_runs_sync() -> int:
    with get_connection() as conn:
        return int(conn.execute(
            f"SELECT COUNT(*) c FROM {_TABLE}").fetchone()["c"])


# ============================================================
# 统计
# ============================================================

def _rows_sync() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, boot_at, last_heartbeat_at, shutdown_at, source "
            f"FROM {_TABLE} ORDER BY boot_at"
        ).fetchall()
    return [dict(r) for r in rows]


def service_uptime_sync() -> Dict[str, Any]:
    """
    累计运行时长。

    total_seconds = Σ(每段结束时间 - boot_at)；
    未结束的段取 last_heartbeat_at（正在跑的那段就是「到现在」）。
    """
    rows = _rows_sync()
    now = datetime.now(timezone.utc)
    total = 0.0
    boots = 0
    first_boot: Optional[str] = None
    running_since: Optional[str] = None

    for r in rows:
        boot = _parse(r["boot_at"])
        if boot is None:
            continue
        boots += 1
        if first_boot is None:
            first_boot = r["boot_at"]

        end = _parse(r["shutdown_at"]) or _parse(r["last_heartbeat_at"])
        if r["shutdown_at"] is None:
            # 仍在运行：按当前时间计，而不是最后一次心跳
            end = now
            running_since = r["boot_at"]
        if end is None:
            continue
        total += max(0.0, (end - boot).total_seconds())

    return {
        "total_uptime_seconds": int(total),
        "boot_count": boots,
        "first_boot_at": first_boot,
        "running_since": running_since,
        "since_first_boot_seconds": int(
            (now - _parse(first_boot)).total_seconds()) if first_boot else 0,
    }
