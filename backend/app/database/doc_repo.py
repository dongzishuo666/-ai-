# ============================================================
# 文件：backend/app/database/doc_repo.py
# 说明：功能一（文档 Prompt 生成）同步数据访问层。
# 约束：所有函数为同步阻塞函数，调用方必须用 asyncio.to_thread 包装。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.database.connection import get_connection, get_transaction


# 非终结态（用于 process 的 TASK_BUSY 判定与恢复）
ACTIVE_STATUSES: tuple = ("pending", "running")

# 允许删除的状态（与蓝图 1.7 一致）
DELETABLE_STATUSES: tuple = ("done", "saved", "failed")


# ============================================================
# 主表：doc_tasks
# ============================================================

def insert_doc_task_sync(
    *,
    filename: str,
    doc_type: str,
    file_size: int,
    file_path: str,
    char_count: int,
    raw_text: str,
) -> Dict[str, Any]:
    """插入文档任务（初始 status='pending'）。返回 {task_id, created_at, status}。"""
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO doc_tasks
                (filename, doc_type, file_size, file_path,
                 char_count, raw_text, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (filename, doc_type, file_size, file_path, char_count, raw_text),
        )
        task_id = cur.lastrowid
        row = conn.execute(
            "SELECT created_at, status FROM doc_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return {
        "task_id": task_id,
        "created_at": row["created_at"],
        "status": row["status"],
    }


def get_doc_task_sync(task_id: int) -> Optional[Dict[str, Any]]:
    """查询文档任务主表。不存在返回 None。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id AS task_id, filename, doc_type, file_size, file_path,
                   char_count, raw_text, summary, user_need, final_prompt,
                   status, error_message, total_duration_ms,
                   created_at, updated_at, completed_at
            FROM doc_tasks WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def list_doc_tasks_sync(page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """分页查询文档任务列表（不含 raw_text / final_prompt 大字段）。"""
    offset = (page - 1) * page_size
    with get_connection() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM doc_tasks").fetchone()
        total = int(total_row["c"]) if total_row else 0
        rows = conn.execute(
            """
            SELECT id AS task_id, filename, doc_type, status,
                   user_need, created_at, completed_at
            FROM doc_tasks
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


def count_active_doc_tasks_sync() -> int:
    """统计处于非终结态（pending / running）的文档任务数。"""
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM doc_tasks WHERE status IN ({placeholders})",
            ACTIVE_STATUSES,
        ).fetchone()
    return int(row["c"]) if row else 0


def update_doc_task_status_sync(
    task_id: int,
    new_status: str,
    *,
    expected_status: Optional[str] = None,
    error_message: Optional[str] = None,
    completed: bool = False,
) -> bool:
    """原子更新文档任务状态。返回是否更新成功。"""
    sets: List[str] = [
        "status = ?",
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')",
    ]
    params: List[Any] = [new_status]

    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)
    if completed:
        sets.append("completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")

    params.append(task_id)
    sql = f"UPDATE doc_tasks SET {', '.join(sets)} WHERE id = ?"
    if expected_status is not None:
        sql += " AND status = ?"
        params.append(expected_status)

    with get_transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


def update_doc_task_user_need_sync(task_id: int, user_need: str) -> None:
    """process 接口写入用户需求。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE doc_tasks
            SET user_need = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (user_need, task_id),
        )


def update_doc_task_final_prompt_sync(
    task_id: int, final_prompt: str, total_duration_ms: int
) -> None:
    """流水线完成后写入最终 Prompt 与总耗时。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE doc_tasks
            SET final_prompt = ?, total_duration_ms = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (final_prompt, total_duration_ms, task_id),
        )


def update_doc_task_summary_sync(task_id: int, summary: str) -> None:
    """（预留）写入文档摘要。当前流程在 process 阶段由 AI 生成摘要后再调用。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE doc_tasks
            SET summary = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (summary, task_id),
        )


def delete_doc_task_sync(task_id: int) -> bool:
    """
    删除文档任务（含 steps，靠 CASCADE）。
    仅允许删除 done / saved / failed 状态。
    """
    placeholders = ",".join("?" for _ in DELETABLE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"DELETE FROM doc_tasks WHERE id = ? AND status IN ({placeholders})",
            (task_id, *DELETABLE_STATUSES),
        )
        return cur.rowcount > 0


# ============================================================
# 子表：doc_steps
# ============================================================

def seed_doc_steps_sync(
    task_id: int, *, planner: str, optimizer: str, finalizer: str
) -> None:
    """
    一次性插入三步 pending 记录。幂等（INSERT OR IGNORE + UNIQUE 约束）。
    """
    rows = [
        (task_id, 1, "planner", planner),
        (task_id, 2, "optimizer", optimizer),
        (task_id, 3, "finalizer", finalizer),
    ]
    with get_transaction() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO doc_steps
                (task_id, step_number, role, model, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            rows,
        )


def mark_doc_step_running_sync(step_id: int, input_prompt: Optional[str] = None) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE doc_steps
            SET status = 'running',
                started_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                input_prompt = ?
            WHERE id = ?
            """,
            (input_prompt, step_id),
        )


def mark_doc_step_done_sync(
    step_id: int,
    *,
    output_content: str,
    output_preview: str,
    duration_ms: int,
    actual_model: Optional[str] = None,
) -> None:
    """
    标记步骤完成。

    actual_model：实际产出该步骤的模型（降级后与计划模型不同）。
    步骤创建时写入的是「计划模型」，不回写会让用户在额度耗尽期间
    误以为中转模型仍在正常工作。
    """
    with get_transaction() as conn:
        if actual_model:
            conn.execute(
                """
                UPDATE doc_steps
                SET status = 'done',
                    model = ?,
                    output_content = ?,
                    output_preview = ?,
                    duration_ms = ?,
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE id = ?
                """,
                (actual_model, output_content, output_preview, duration_ms, step_id),
            )
            return
        conn.execute(
            """
            UPDATE doc_steps
            SET status = 'done',
                output_content = ?,
                output_preview = ?,
                duration_ms = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (output_content, output_preview, duration_ms, step_id),
        )


def mark_doc_step_failed_sync(step_id: int, error_message: str) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE doc_steps
            SET status = 'failed',
                error_message = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (error_message, step_id),
        )


def mark_doc_step_skipped_sync(step_id: int, reason: str) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE doc_steps
            SET status = 'skipped',
                error_message = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (reason, step_id),
        )


def get_doc_steps_sync(task_id: int) -> List[Dict[str, Any]]:
    """查询任务的三个步骤，按 step_number 排序。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, step_number, role, model, status,
                   output_preview, duration_ms, error_message,
                   started_at, completed_at
            FROM doc_steps WHERE task_id = ?
            ORDER BY step_number ASC
            """,
            (task_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 启动时清理僵尸任务（对照功能三 reset_stale_articles_sync）
# ============================================================

def reset_stale_doc_tasks_sync() -> int:
    """
    将停留在非终结态的文档任务重置为 failed。
    防止服务崩溃后任务永久阻塞。
    """
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            UPDATE doc_tasks
            SET status = 'failed',
                error_message = '服务重启导致任务中断',
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE status IN ({placeholders})
            """,
            ACTIVE_STATUSES,
        )
        return cur.rowcount
