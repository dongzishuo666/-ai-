# ============================================================
# 文件：backend/app/database/article_repo.py
# 说明：功能三（文章生成）同步数据访问层。
# 约束：所有函数为同步阻塞函数，调用方必须用 asyncio.to_thread 包装。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.database.connection import get_connection, get_transaction


# 非终结态（用于 TASK_BUSY 判定与恢复逻辑）
ACTIVE_STATUSES: tuple = (
    "created", "outlining", "outline_ready", "writing", "revising",
)

# 允许删除的状态
DELETABLE_STATUSES: tuple = ("done", "failed")


# ============================================================
# 主表：articles
# ============================================================

def count_active_articles_sync() -> int:
    """统计处于非终结态的文章数量（用于 409 TASK_BUSY 判定）。"""
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM articles WHERE status IN ({placeholders})",
            ACTIVE_STATUSES,
        ).fetchone()
    return int(row["c"]) if row else 0


def insert_article_sync(
    *,
    topic: str,
    genre: str,
    target_word_count: int,
    style: Optional[str],
    audience: Optional[str],
    additional_requirements: Optional[str],
) -> Dict[str, Any]:
    """
    插入文章主记录，状态固定为 'created'。
    返回：{article_id, created_at, status}
    """
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO articles
                (topic, genre, target_word_count, style, audience,
                 additional_requirements, status)
            VALUES (?, ?, ?, ?, ?, ?, 'created')
            """,
            (topic, genre, target_word_count, style, audience,
             additional_requirements),
        )
        article_id = cur.lastrowid
        row = conn.execute(
            "SELECT created_at, status FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    return {
        "article_id": article_id,
        "created_at": row["created_at"],
        "status": row["status"],
    }


def get_article_sync(article_id: int) -> Optional[Dict[str, Any]]:
    """
    查询文章主表。不存在返回 None。
    不含 stages 子表数据（阶段详情由 get_stages_sync 单独获取）。
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id AS article_id, topic, genre, target_word_count,
                   style, audience, additional_requirements, status,
                   final_word_count, total_duration_ms, error_message,
                   created_at, updated_at, completed_at
            FROM articles WHERE id = ?
            """,
            (article_id,),
        ).fetchone()
    return dict(row) if row else None


def list_articles_sync(
    page: int = 1, page_size: int = 20
) -> Dict[str, Any]:
    """
    分页查询文章列表（不含 stages）。
    返回：{total, page, page_size, items}
    """
    offset = (page - 1) * page_size
    with get_connection() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()
        total = int(total_row["c"]) if total_row else 0

        rows = conn.execute(
            """
            SELECT id AS article_id, topic, genre, status,
                   final_word_count, created_at, completed_at
            FROM articles
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


def update_article_status_sync(
    article_id: int,
    new_status: str,
    *,
    expected_status: Optional[str] = None,
    error_message: Optional[str] = None,
    completed: bool = False,
) -> bool:
    """
    原子更新文章状态。
    - expected_status 非空时，仅当当前状态匹配才更新（防并发冲突）
    - completed=True 时同时写入 completed_at
    - error_message 非空时写入错误字段
    返回：是否更新成功（rowcount > 0）
    """
    sets: List[str] = ["status = ?", "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"]
    params: List[Any] = [new_status]

    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)

    if completed:
        sets.append("completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")

    params.append(article_id)

    sql = f"UPDATE articles SET {', '.join(sets)} WHERE id = ?"
    if expected_status is not None:
        sql += " AND status = ?"
        params.append(expected_status)

    with get_transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


def update_article_final_word_count_sync(
    article_id: int, word_count: int, total_duration_ms: int
) -> None:
    """阶段全部完成后写入最终字数与总耗时。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE articles
            SET final_word_count = ?, total_duration_ms = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (word_count, total_duration_ms, article_id),
        )


def delete_article_sync(article_id: int) -> bool:
    """
    删除文章（含 stages，靠 ON DELETE CASCADE）。
    仅允许删除 done / failed 状态。
    返回：是否删除成功。
    """
    placeholders = ",".join("?" for _ in DELETABLE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"DELETE FROM articles WHERE id = ? AND status IN ({placeholders})",
            (article_id, *DELETABLE_STATUSES),
        )
        return cur.rowcount > 0


# ============================================================
# 子表：article_stages
# ============================================================

def insert_stage_sync(
    *, article_id: int, stage: str, role: str, model: str
) -> int:
    """插入阶段记录（初始状态 pending），返回 stage_id。"""
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO article_stages
                (article_id, stage, role, model, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (article_id, stage, role, model),
        )
        return cur.lastrowid


def mark_stage_running_sync(stage_id: int, input_content: Optional[str] = None) -> None:
    """将阶段标记为 running，并写入输入内容（可选）。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE article_stages
            SET status = 'running',
                started_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                input_content = ?
            WHERE id = ?
            """,
            (input_content, stage_id),
        )


def mark_stage_done_sync(
    stage_id: int,
    *,
    output_content: str,
    word_count: int,
    duration_ms: int,
    revision_notes: Optional[str] = None,
    actual_model: Optional[str] = None,
) -> None:
    """
    将阶段标记为 done，写入输出与耗时。

    actual_model：实际产出该阶段的模型（降级后与计划模型不同）。
    阶段创建时写入的是「计划模型」，不回写会让用户误以为首选模型仍在工作。
    """
    with get_transaction() as conn:
        if actual_model:
            conn.execute(
                """
                UPDATE article_stages
                SET status = 'done',
                    model = ?,
                    output_content = ?,
                    word_count = ?,
                    duration_ms = ?,
                    revision_notes = ?,
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE id = ?
                """,
                (actual_model, output_content, word_count, duration_ms,
                 revision_notes, stage_id),
            )
            return
        conn.execute(
            """
            UPDATE article_stages
            SET status = 'done',
                output_content = ?,
                word_count = ?,
                duration_ms = ?,
                revision_notes = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (output_content, word_count, duration_ms, revision_notes, stage_id),
        )


def mark_stage_failed_sync(stage_id: int, error_message: str) -> None:
    """将阶段标记为 failed。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE article_stages
            SET status = 'failed',
                error_message = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (error_message, stage_id),
        )


def get_stages_sync(article_id: int) -> List[Dict[str, Any]]:
    """查询某文章的全部阶段记录，按 stage 固定顺序返回。"""
    order = {"outline": 1, "draft": 2, "revision": 3}
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, stage, role, model, status, output_content,
                   word_count, revision_notes, duration_ms, error_message,
                   started_at, completed_at
            FROM article_stages WHERE article_id = ?
            """,
            (article_id,),
        ).fetchall()
    items = [dict(r) for r in rows]
    items.sort(key=lambda x: order.get(x["stage"], 99))
    return items


# ============================================================
# 追加：批次2 所需的两个辅助函数
# ============================================================

def seed_stages_sync(article_id: int, *, outliner: str, writer: str, reviewer: str) -> None:
    """
    一次性插入三阶段 pending 记录。
    - 若已存在（如重连场景），仅插入不存在的阶段（靠 UNIQUE 约束 + INSERT OR IGNORE）
    """
    rows = [
        (article_id, "outline", "outliner", outliner),
        (article_id, "draft", "writer", writer),
        (article_id, "revision", "reviewer", reviewer),
    ]
    with get_transaction() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO article_stages
                (article_id, stage, role, model, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            rows,
        )


def reset_stale_articles_sync() -> int:
    """
    应用启动时，把停留在非终结态的文章统一重置为 failed。
    防止服务崩溃后任务永久阻塞 create（提醒1）。
    返回重置数量。
    """
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            UPDATE articles
            SET status = 'failed',
                error_message = '服务重启导致任务中断',
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE status IN ({placeholders})
            """,
            ACTIVE_STATUSES,
        )
        return cur.rowcount
