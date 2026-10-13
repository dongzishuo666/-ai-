# ============================================================
# 文件：backend/app/database/score_repo.py
# 说明：统一评分记录访问层。
#       三个功能的「最终分」都落在一张表里：
#         feature=discussion → 讨论终局评价裁判给每个席位打的最终分
#         feature=article    → 文章流水线的质量检测员给三个环节 AI 打的质检分
#         feature=doc        → 文档流水线的质量检测员给三个环节 AI 打的质检分
#       这样「历史榜 / 平时分」才能跨任务聚合，供用户讨论前选人参考。
# 约束：同步 sqlite3，调用方必须用 asyncio.to_thread 包装。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.database.connection import get_connection, get_transaction

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ai_score_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    feature      TEXT NOT NULL,          -- discussion / article / doc
    task_id      INTEGER NOT NULL,       -- discussion_id / article_id / doc task_id
    ai_id        TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT '',   -- discussant / outliner / writer / reviewer ...
    score        REAL NOT NULL DEFAULT 0,    -- 0-100 最终分
    grade        TEXT NOT NULL DEFAULT '',   -- S / A / B / C
    comment      TEXT NOT NULL DEFAULT '',   -- 一句话评价
    scored_by    TEXT NOT NULL DEFAULT '',   -- 打分者（裁判/质检员）ai_id
    overall      REAL NOT NULL DEFAULT 0,    -- 成品总评分（质检员给出；讨论场景为 0）
    summary      TEXT NOT NULL DEFAULT '',   -- 质检总结（同一任务的每条记录冗余存同一份）
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_aisr_task ON ai_score_records(feature, task_id)",
    "CREATE INDEX IF NOT EXISTS idx_aisr_ai ON ai_score_records(ai_id)",
)

# 线上库建表时还没有这两列，这里做一次幂等补列，避免手改 DB
_EXTRA_COLUMNS = (
    ("overall", "REAL NOT NULL DEFAULT 0"),
    ("summary", "TEXT NOT NULL DEFAULT ''"),
)


def ensure_tables_sync() -> None:
    """
    建表（幂等）。

    init.sql 只在首次建库时执行，已存在的线上库靠这里补齐，避免手改 DB。
    """
    with get_transaction() as conn:
        conn.execute(_CREATE_SQL)
        for sql in _INDEX_SQL:
            conn.execute(sql)
        existing = {
            r[1] for r in conn.execute("PRAGMA table_info(ai_score_records)")
        }
        for col, ddl in _EXTRA_COLUMNS:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE ai_score_records ADD COLUMN {col} {ddl}")


def replace_task_scores_sync(feature: str, task_id: int,
                             records: List[Dict[str, Any]],
                             overall: float = 0.0,
                             summary: str = "") -> int:
    """
    覆盖写入某任务的榜单（重跑时不会出现两份）。

    overall / summary 是这场任务的整体信息（质检员的成品总评分与总结），
    冗余写在每条记录上，便于回看历史任务时一次查全。
    """
    with get_transaction() as conn:
        conn.execute(
            "DELETE FROM ai_score_records WHERE feature=? AND task_id=?",
            (feature, task_id))
        if not records:
            return 0
        rows = []
        for r in records:
            item = dict(r)
            item.setdefault("overall", overall)
            item.setdefault("summary", summary)
            rows.append(item)
        conn.executemany(
            """
            INSERT INTO ai_score_records
                (feature, task_id, ai_id, display_name, role,
                 score, grade, comment, scored_by, overall, summary)
            VALUES
                (:feature, :task_id, :ai_id, :display_name, :role,
                 :score, :grade, :comment, :scored_by, :overall, :summary)
            """,
            rows,
        )
        return len(rows)


def get_task_scores_sync(feature: str, task_id: int) -> List[Dict[str, Any]]:
    """某场任务的最终榜（按分数降序）。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, feature, task_id, ai_id, display_name, role,
                   score, grade, comment, scored_by, created_at
            FROM ai_score_records
            WHERE feature = ? AND task_id = ?
            ORDER BY score DESC, id
            """,
            (feature, task_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_task_meta_sync(feature: str, task_id: int) -> Dict[str, Any]:
    """
    某场任务的整体信息：成品总评分 + 质检总结。

    整体信息冗余存在每条记录上，这里取最大值即可（同一任务值相同）。
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(overall) AS overall, MAX(summary) AS summary
            FROM ai_score_records WHERE feature = ? AND task_id = ?
            """,
            (feature, task_id),
        ).fetchone()
    if row is None:
        return {"overall": None, "summary": ""}
    data = dict(row)
    overall = data.get("overall")
    return {
        "overall": float(overall) if overall else None,
        "summary": data.get("summary") or "",
    }


def get_ai_ranking_sync(feature: Optional[str] = None,
                        limit: int = 50) -> List[Dict[str, Any]]:
    """
    历史总榜：按 AI 聚合出「平时分」。

    feature 为空表示跨功能汇总。avg_score 即前端展示的平时分，
    rounds 是参与场次（场次太少时前端会标注样本不足）。
    """
    sql = """
        SELECT ai_id,
               MAX(display_name)                     AS display_name,
               COUNT(*)                              AS rounds,
               ROUND(AVG(score), 1)                  AS avg_score,
               ROUND(MAX(score), 1)                  AS best_score,
               ROUND(MIN(score), 1)                  AS worst_score,
               MAX(created_at)                       AS last_scored_at
        FROM ai_score_records
    """
    params: List[Any] = []
    if feature:
        sql += " WHERE feature = ?"
        params.append(feature)
    sql += " GROUP BY ai_id ORDER BY avg_score DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_ai_history_sync(ai_id: str, feature: Optional[str] = None,
                        limit: int = 10) -> List[Dict[str, Any]]:
    """单个 AI 的近期得分（用于榜单弹窗里的明细）。"""
    sql = """
        SELECT feature, task_id, role, score, grade, comment, created_at
        FROM ai_score_records
        WHERE ai_id = ?
    """
    params: List[Any] = [ai_id]
    if feature:
        sql += " AND feature = ?"
        params.append(feature)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_recent_tasks_sync(feature: Optional[str] = None,
                          limit: int = 10) -> List[Dict[str, Any]]:
    """最近完成的任务（榜单弹窗里可切换查看某一场的榜）。"""
    sql = """
        SELECT feature, task_id, MAX(created_at) AS created_at,
               COUNT(*) AS seat_count
        FROM ai_score_records
    """
    params: List[Any] = []
    if feature:
        sql += " WHERE feature = ?"
        params.append(feature)
    sql += " GROUP BY feature, task_id ORDER BY MAX(id) DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
