# ============================================================
# 文件：backend/app/database/discussion_repo.py
# 说明：功能二（多 AI 讨论）同步数据访问层。
# 约束：所有函数为同步阻塞函数，调用方必须用 asyncio.to_thread 包装。
# 依据：DDL v3（round_scores 宽表 / discussion_bans 事件流式）
# ============================================================

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.config import AI_MODELS
from app.config.business import (
    DISCUSSION_JUDGES,
    DISCUSSION_PARTICIPANTS,
)
from app.database.connection import get_connection, get_transaction


# 非终结态（用于 create 的 TASK_BUSY 判定与启动重置）
ACTIVE_STATUSES: tuple = ("created", "running", "paused")

# 允许删除的状态（与蓝图 2.8 一致）
DELETABLE_STATUSES: tuple = ("done", "terminated", "failed")


# ------------------------------------------------------------
# 裁判元数据映射（display_name / duty 取自蓝图 2.1 字面值）
# 说明：DISCUSSION_JUDGES 中仅有 ai_id/fallback/description；
#       响应层需独立的 display_name 与 duty，故在此维护。
# ------------------------------------------------------------
_JUDGE_META: Dict[str, Dict[str, str]] = {
    "compliance": {"display_name": "实时监督裁判", "duty": "发言合规检测"},
    "order":      {"display_name": "秩序裁判",     "duty": "顺序管控与计分"},
    "evaluation": {"display_name": "最终评价裁判", "duty": "终局评估与总结"},
}


# ------------------------------------------------------------
# discussions 表列缓存（兼容 V5 迁移未执行的旧库）
# ------------------------------------------------------------
_DISC_COLUMNS: Optional[set] = None


def _discussion_columns(conn) -> set:
    global _DISC_COLUMNS
    if _DISC_COLUMNS is None:
        _DISC_COLUMNS = {
            r["name"] for r in conn.execute("PRAGMA table_info(discussions)")
        }
    return _DISC_COLUMNS


# ============================================================
# 参与者配置组装（create 时调用）
# ============================================================

def _resolve_participant_ids(participant_ids: Optional[List[str]]) -> List[str]:
    """
    规范化讨论者列表：校验已注册、截断/补足到 DISCUSSION_PARTICIPANT_COUNT(3)。

    允许同一个模型占据多个席位（用户自选阵容，可重复）——原先的去重会静默丢弃
    用户的选择，改为保序保留；重复席位由 build_participant_rows_sync 加序号区分。
    未指定时用 DISCUSSION_DEFAULT_PARTICIPANTS。
    """
    from app.config.business import (
        DISCUSSION_DEFAULT_PARTICIPANTS,
        DISCUSSION_PARTICIPANT_COUNT,
        DISCUSSION_PARTICIPANTS,
    )

    raw = list(participant_ids) if participant_ids else list(DISCUSSION_DEFAULT_PARTICIPANTS)

    # 只过滤未注册的 id，不再去重
    cleaned: List[str] = [ai_id for ai_id in raw if ai_id and ai_id in AI_MODELS]

    # 补足到 N 位（允许与已选重复，避免用户选了重复模型后被悄悄换成别家）
    for ai_id in DISCUSSION_PARTICIPANTS:
        if len(cleaned) >= DISCUSSION_PARTICIPANT_COUNT:
            break
        cleaned.append(ai_id)

    return cleaned[:DISCUSSION_PARTICIPANT_COUNT]


def build_participant_rows_sync(
    participant_ids: Optional[List[str]] = None,
    judge_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    组装 participant 数据：3 条 discussant + 3 条 judge。
    - discussant：由 participant_ids 指定（默认 DISCUSSION_DEFAULT_PARTICIPANTS 的 3 位）
    - judge：由 judge_map 覆盖，缺省项回落到 DISCUSSION_JUDGES 配置
    """
    rows: List[Dict[str, Any]] = []

    # N 条 discussant（默认 3，允许同一模型重复占位）
    discussant_ids = _resolve_participant_ids(participant_ids)
    seat_count: Dict[str, int] = {}
    for ai_id in discussant_ids:
        seat_count[ai_id] = seat_count.get(ai_id, 0) + 1
    seat_index: Dict[str, int] = {}

    for ai_id in discussant_ids:
        cfg = AI_MODELS.get(ai_id)
        if cfg is None:
            raise RuntimeError(f"参与者 {ai_id} 未在 AI_MODELS 注册")
        display_name = cfg.display_name
        # 同一模型占多个席位时加序号，否则消息流与评分表无法区分谁是谁
        if seat_count[ai_id] > 1:
            seat_index[ai_id] = seat_index.get(ai_id, 0) + 1
            display_name = f"{cfg.display_name} {seat_index[ai_id]}"
        rows.append({
            "ai_id": ai_id,
            "display_name": display_name,
            "channel": cfg.channel,
            "role": "discussant",
            "judge_type": None,
        })

    # 3 条 judge
    judge_map = judge_map or {}
    for judge_type, jcfg in DISCUSSION_JUDGES.items():
        ai_id = judge_map.get(judge_type) or jcfg["ai_id"]
        cfg = AI_MODELS.get(ai_id)
        if cfg is None:
            raise RuntimeError(f"裁判 {judge_type} 的 {ai_id} 未在 AI_MODELS 注册")
        meta = _JUDGE_META.get(judge_type, {})
        rows.append({
            "ai_id": ai_id,
            "display_name": meta.get("display_name", cfg.display_name),
            "channel": cfg.channel,
            "role": "judge",
            "judge_type": judge_type,
        })

    return rows


# ============================================================
# 主表：discussions
# ============================================================

def count_active_discussions_sync() -> int:
    """统计处于非终结态的讨论数（用于 409 TASK_BUSY 判定）。"""
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM discussions WHERE status IN ({placeholders})",
            ACTIVE_STATUSES,
        ).fetchone()
    return int(row["c"]) if row else 0


def reset_stale_discussions_sync(reason: str = "服务重启导致任务中断") -> int:
    """
    将全部非终结态讨论重置为终止态。

    仅在服务启动时调用：上一进程被 kill / 崩溃后，其流水线 asyncio 任务随之消失，
    但库里的 status 仍停留在 running/created/paused，会永久占用全局单任务锁。
    """
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            UPDATE discussions
               SET status = 'terminated',
                   end_reason = 'error',
                   error_message = ?,
                   completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE status IN ({placeholders})
            """,
            [reason, *ACTIVE_STATUSES],
        )
        return cur.rowcount


def reap_stale_discussions_sync(stale_seconds: int = 1800) -> int:
    """
    回收僵尸讨论：非终结态、且最后活动时间早于阈值的记录置为终止态。

    「最后活动」取「最新一条发言时间」与「状态推进时间（resumed/paused/started/created）」
    的较大者——评分等长耗时环节可能数分钟不产生新消息，只看消息会误杀，
    只看状态时间又会把已跑很久却仍在推进的任务漏掉。

    返回被回收的记录数。
    """
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    stale = int(stale_seconds)
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            UPDATE discussions
               SET status = 'terminated',
                   end_reason = 'error',
                   error_message = '僵尸任务自动回收：长时间无进展',
                   completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE status IN ({placeholders})
               AND COALESCE(
                     (SELECT MAX(created_at) FROM discussion_messages
                       WHERE discussion_id = discussions.id),
                     COALESCE(resumed_at,
                              COALESCE(paused_at,
                                       COALESCE(started_at, created_at)))
                   ) < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-{stale} seconds')
            """,
            ACTIVE_STATUSES,
        )
        return cur.rowcount


def insert_discussion_with_participants_sync(
    *,
    topic: str,
    mode: str,
    total_rounds: int,
    max_duration_seconds: int,
    style_mode: str = "free",
    until_consensus: bool = False,
    memory_enabled: bool = False,
    participant_ids: Optional[List[str]] = None,
    judge_map: Optional[Dict[str, str]] = None,
    consensus_strictness: str = "medium",
) -> Dict[str, Any]:
    """
    在同一事务中：
      1. 插入 discussions 主表（status='created'）
      2. 插入 3 条 discussant + 3 条 judge
    返回：{discussion_id, topic, status, total_rounds, mode, created_at}
    """
    participant_rows = build_participant_rows_sync(participant_ids, judge_map)

    with get_transaction() as conn:
        # 1. 插入 discussions
        #    consensus_strictness 为 V5 新增列；若迁移未执行则自动降级，不阻断创建
        cols = ["topic", "mode", "total_rounds", "max_duration_seconds", "status",
                "style_mode", "until_consensus", "memory_enabled"]
        vals: List[Any] = [topic, mode, total_rounds, max_duration_seconds, "created",
                           style_mode, int(until_consensus), int(memory_enabled)]
        if "consensus_strictness" in _discussion_columns(conn):
            cols.append("consensus_strictness")
            vals.append(consensus_strictness)

        cur = conn.execute(
            f"""
            INSERT INTO discussions ({", ".join(cols)})
            VALUES ({", ".join("?" for _ in cols)})
            """,
            vals,
        )
        discussion_id = cur.lastrowid

        # 2. 批量插入 participants（3 discussant + 3 judge）
        conn.executemany(
            """
            INSERT INTO discussion_participants
                (discussion_id, ai_id, display_name, channel, role, judge_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    discussion_id,
                    p["ai_id"],
                    p["display_name"],
                    p["channel"],
                    p["role"],
                    p["judge_type"],
                )
                for p in participant_rows
            ],
        )

        # 3. 读回 created_at
        row = conn.execute(
            "SELECT created_at, status FROM discussions WHERE id = ?",
            (discussion_id,),
        ).fetchone()

    return {
        "discussion_id": discussion_id,
        "topic": topic,
        "status": row["status"],
        "total_rounds": total_rounds,
        "mode": mode,
        "created_at": row["created_at"],
    }


logger = logging.getLogger(__name__)


def ensure_discussion_columns_sync() -> None:
    """
    补建 discussions 的新增列（midterm_summary：局中总结）。

    init.sql 只在首次建库时执行，已存在的库靠这里补齐，避免手改线上 DB。
    """
    with get_transaction() as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(discussions)").fetchall()}
        if "midterm_summary" not in cols:
            conn.execute("ALTER TABLE discussions ADD COLUMN midterm_summary TEXT")


def _rename_table_in_ddl(create_sql: str, new_name: str) -> str:
    """把 CREATE TABLE 语句里的表名换成 new_name（保留 IF NOT EXISTS）。"""
    return re.sub(
        r"(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?)discussions\b",
        lambda m: m.group(1) + new_name,
        create_sql, count=1, flags=re.IGNORECASE,
    )


def ensure_end_reason_check_sync() -> None:
    """
    给 discussions.end_reason 的 CHECK 增加 'pause_timeout'。

    暂停超时以前被记成 user_manual（用户手动终止），终局评价裁判与统计
    拿到的结束原因都是错的。SQLite 改 CHECK 只能重建表，这里按库里当前的
    表定义自动生成新 DDL（避免手写漏列），索引 / 触发器一并重建。

    重建父表必须先关外键：否则 DROP TABLE 会级联删掉子表数据。
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='discussions'"
        ).fetchone()
        if row is None or not row["sql"]:
            return
        create_sql = row["sql"]
        if "pause_timeout" in create_sql:
            return                                   # 已是新约束，幂等

        new_sql = re.sub(
            r"(end_reason[^,]*?CHECK\s*\(\s*end_reason\s+IN\s*\()([^)]*)\)",
            lambda m: m.group(1) + m.group(2) + ", 'pause_timeout')",
            create_sql, flags=re.IGNORECASE,
        )
        if new_sql == create_sql:
            logger.warning("未匹配到 end_reason 的 CHECK，跳过迁移")
            return

        cols = [r["name"] for r in conn.execute(
            "PRAGMA table_info(discussions)")]
        indexes = [r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='discussions' AND sql IS NOT NULL")]
        triggers = [r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='discussions'")]

    quoted = ", ".join(f'"{c}"' for c in cols)
    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute(_rename_table_in_ddl(new_sql, "discussions_new"))
            conn.execute(
                f"INSERT INTO discussions_new ({quoted}) "
                f"SELECT {quoted} FROM discussions")
            conn.execute("DROP TABLE discussions")
            conn.execute("ALTER TABLE discussions_new RENAME TO discussions")
            for sql in indexes:
                conn.execute(sql)
            for sql in triggers:
                conn.execute(sql)
            conn.execute("COMMIT")
            logger.info("discussions.end_reason 已支持 pause_timeout")
        except Exception:
            conn.execute("ROLLBACK")
            logger.exception("discussions 重建失败，已回滚")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def update_midterm_summary_sync(discussion_id: int, summary_json: str) -> None:
    """落库局中总结（JSON 文本），供收敛期注入与前端展示。"""
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET midterm_summary = ? WHERE id = ?",
            (summary_json, discussion_id),
        )


def set_consensus_reached_sync(discussion_id: int) -> None:
    """标记讨论已达成共识（扩展V4）。"""
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET consensus_reached = 1 WHERE id = ?",
            (discussion_id,),
        )


def get_discussion_sync(discussion_id: int) -> Optional[Dict[str, Any]]:
    """查询讨论主表（不含 participants/messages）。用 SELECT * 以自动包含新增列。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT *, id AS discussion_id FROM discussions WHERE id = ?",
            (discussion_id,),
        ).fetchone()
    return dict(row) if row else None


def list_discussions_sync(page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """分页查询讨论列表（不含 participants/messages）。"""
    offset = (page - 1) * page_size
    with get_connection() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM discussions").fetchone()
        total = int(total_row["c"]) if total_row else 0

        rows = conn.execute(
            """
            SELECT id AS discussion_id, topic, mode, status, end_reason,
                   total_rounds, rounds_completed, created_at, completed_at
            FROM discussions
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


def update_discussion_status_sync(
    discussion_id: int,
    new_status: str,
    *,
    expected_status: Optional[str] = None,
    end_reason: Optional[str] = None,
    error_message: Optional[str] = None,
    completed: bool = False,
    touch_started: bool = False,
    touch_paused: bool = False,
    touch_resumed: bool = False,
) -> bool:
    """
    原子更新讨论状态。返回是否更新成功。
    - expected_status：乐观锁（仅当状态匹配时更新）
    - completed=True：同时写 completed_at
    - touch_started / touch_paused / touch_resumed：写对应时间戳
    """
    sets: List[str] = ["status = ?"]
    params: List[Any] = [new_status]

    if end_reason is not None:
        sets.append("end_reason = ?")
        params.append(end_reason)
    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)
    if completed:
        sets.append("completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    if touch_started:
        sets.append("started_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    if touch_paused:
        sets.append("paused_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    if touch_resumed:
        sets.append("resumed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")

    params.append(discussion_id)
    sql = f"UPDATE discussions SET {', '.join(sets)} WHERE id = ?"
    if expected_status is not None:
        sql += " AND status = ?"
        params.append(expected_status)

    with get_transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


def delete_discussion_sync(discussion_id: int) -> bool:
    """
    删除讨论（含所有子表，靠 ON DELETE CASCADE）。
    仅允许 done / terminated / failed。
    """
    placeholders = ",".join("?" for _ in DELETABLE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"DELETE FROM discussions WHERE id = ? AND status IN ({placeholders})",
            (discussion_id, *DELETABLE_STATUSES),
        )
        return cur.rowcount > 0


# ============================================================
# 参与者：discussion_participants
# ============================================================

def get_participants_sync(discussion_id: int) -> List[Dict[str, Any]]:
    """
    查询某讨论的全部参与者（含 discussant 与 judge）。
    按 role 与 ai_id 排序，供响应层分组使用。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, ai_id, display_name, channel, role, judge_type,
                   is_banned, ban_start_round, ban_end_round,
                   total_score, final_rank
            FROM discussion_participants
            WHERE discussion_id = ?
            ORDER BY role DESC, id ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 启动时清理僵尸讨论（对照 reset_stale_articles/doc_tasks）
# ============================================================

def reset_stale_discussions_sync() -> int:
    """
    将停留在非终结态（created / running / paused）的讨论重置为 failed。
    防止服务崩溃后 created/running 状态永久阻塞后续 create。
    """
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with get_transaction() as conn:
        cur = conn.execute(
            f"""
            UPDATE discussions
            SET status = 'failed',
                end_reason = 'error',
                error_message = '服务重启导致讨论中断',
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE status IN ({placeholders})
            """,
            ACTIVE_STATUSES,
        )
        return cur.rowcount


# ============================================================
# 追加：批次2 · 消息 / 评分 / 禁言 / 日志 相关
# ============================================================

from app.config.business import DISCUSSION_CONFIG


# ------------------------------------------------------------
# 消息：discussion_messages
# ------------------------------------------------------------

def insert_message_sync(
    discussion_id: int, round_no: int, message_order: int,
    ai_id: str, display_name: str, content: str, word_count: int,
) -> int:
    """插入一条发言，返回 message_id。"""
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO discussion_messages
                (discussion_id, round, message_order, ai_id, display_name,
                 content, word_count, compliance_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'passed')
            """,
            (discussion_id, round_no, message_order, ai_id, display_name,
             content, word_count),
        )
        return cur.lastrowid


def update_message_compliance_sync(
    message_id: int, status: str,
    reason: Optional[str] = None, violation_type: Optional[str] = None,
) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE discussion_messages
            SET compliance_status = ?, compliance_reason = ?, violation_type = ?
            WHERE id = ?
            """,
            (status, reason, violation_type, message_id),
        )


def update_message_weighted_total_sync(message_id: int, weighted_total: float) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussion_messages SET weighted_total = ? WHERE id = ?",
            (weighted_total, message_id),
        )


def get_messages_in_round_sync(discussion_id: int, round_no: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id AS message_id, round, message_order, ai_id, display_name,
                   content, word_count, compliance_status, compliance_reason,
                   violation_type, weighted_total, created_at
            FROM discussion_messages
            WHERE discussion_id = ? AND round = ?
            ORDER BY message_order ASC
            """,
            (discussion_id, round_no),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_messages_sync(discussion_id: int, limit: int = 8) -> List[Dict[str, Any]]:
    """取最近 N 条发言（时间倒序后再反转，返回正序）。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT ai_id, display_name, round, content
            FROM discussion_messages
            WHERE discussion_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (discussion_id, limit),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def get_all_messages_sync(discussion_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id AS message_id, round, message_order, ai_id, display_name,
                   content, word_count, compliance_status, compliance_reason,
                   violation_type, weighted_total, created_at
            FROM discussion_messages
            WHERE discussion_id = ?
            ORDER BY round ASC, message_order ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_messages_sync(discussion_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM discussion_messages WHERE discussion_id = ?",
            (discussion_id,),
        ).fetchone()
    return int(row["c"]) if row else 0


# ------------------------------------------------------------
# 讨论：current_round / rounds_completed
# ------------------------------------------------------------

def update_discussion_current_round_sync(discussion_id: int, round_no: int) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET current_round = ? WHERE id = ?",
            (round_no, discussion_id),
        )


def increment_rounds_completed_sync(discussion_id: int) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET rounds_completed = rounds_completed + 1 WHERE id = ?",
            (discussion_id,),
        )


# ------------------------------------------------------------
# 参与者：累加分数 / 禁言状态
# ------------------------------------------------------------

def increment_participant_score_sync(discussion_id: int, ai_id: str, delta: float) -> None:
    """累加 discussant 的 total_score。"""
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE discussion_participants
            SET total_score = total_score + ?
            WHERE discussion_id = ? AND ai_id = ? AND role = 'discussant'
            """,
            (delta, discussion_id, ai_id),
        )


def set_participant_banned_sync(
    discussion_id: int, ai_id: str, is_banned: bool,
    ban_start_round: Optional[int] = None, ban_end_round: Optional[int] = None,
) -> None:
    """
    按模型（ai_id）整体禁言 / 解禁该模型的全部席位。

    注意：正常流程请不要用它解除禁言——同一模型占多个席位、只禁其一时，
    它会把另一个席位的禁言一起清掉。解禁请走 set_seat_banned_sync（按席位）。
    保留在此仅供「按模型整体处理」的场景。
    """
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE discussion_participants
            SET is_banned = ?, ban_start_round = ?, ban_end_round = ?
            WHERE discussion_id = ? AND ai_id = ? AND role = 'discussant'
            """,
            (1 if is_banned else 0, ban_start_round, ban_end_round,
             discussion_id, ai_id),
        )


def increment_seat_score_sync(
    discussion_id: int, seat_id: Optional[int], delta: float,
    ai_id: Optional[str] = None,
) -> None:
    """
    累加某个席位的 total_score。

    必须按参与者行 id 更新：同一个模型占多个席位时，按 ai_id 更新会把分数
    同时加到所有同源席位上（一人发言、全员加分）。seat_id 缺失时回退 ai_id。
    """
    with get_transaction() as conn:
        if seat_id is not None:
            conn.execute(
                """
                UPDATE discussion_participants
                SET total_score = total_score + ?
                WHERE discussion_id = ? AND id = ?
                """,
                (delta, discussion_id, seat_id),
            )
        else:
            conn.execute(
                """
                UPDATE discussion_participants
                SET total_score = total_score + ?
                WHERE discussion_id = ? AND ai_id = ? AND role = 'discussant'
                """,
                (delta, discussion_id, ai_id),
            )


def set_seat_banned_sync(
    discussion_id: int, seat_id: Optional[int], is_banned: bool,
    ban_start_round: Optional[int] = None, ban_end_round: Optional[int] = None,
    ai_id: Optional[str] = None,
) -> None:
    """
    禁言 / 解禁某个席位。

    同样按参与者行 id：同源的其他席位不该被连带禁言。
    """
    with get_transaction() as conn:
        if seat_id is not None:
            conn.execute(
                """
                UPDATE discussion_participants
                SET is_banned = ?, ban_start_round = ?, ban_end_round = ?
                WHERE discussion_id = ? AND id = ?
                """,
                (1 if is_banned else 0, ban_start_round, ban_end_round,
                 discussion_id, seat_id),
            )
        else:
            conn.execute(
                """
                UPDATE discussion_participants
                SET is_banned = ?, ban_start_round = ?, ban_end_round = ?
                WHERE discussion_id = ? AND ai_id = ? AND role = 'discussant'
                """,
                (1 if is_banned else 0, ban_start_round, ban_end_round,
                 discussion_id, ai_id),
            )


def get_discussants_sync(discussion_id: int) -> List[Dict[str, Any]]:
    """获取 8 条 discussant（按 id 顺序 = 固定轮询顺序）。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, ai_id, display_name, channel,
                   is_banned, ban_start_round, ban_end_round,
                   total_score, final_rank
            FROM discussion_participants
            WHERE discussion_id = ? AND role = 'discussant'
            ORDER BY id ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_judge_sync(discussion_id: int, judge_type: str) -> Optional[Dict[str, Any]]:
    """按 judge_type 获取裁判记录。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, ai_id, display_name, channel, judge_type
            FROM discussion_participants
            WHERE discussion_id = ? AND role = 'judge' AND judge_type = ?
            """,
            (discussion_id, judge_type),
        ).fetchone()
    return dict(row) if row else None


def update_judge_sync(discussion_id: int, judge_type: str,
                      new_ai_id: str) -> bool:
    """
    替换某裁判席位（弹劾通过后调用）。

    为什么直接改 participants 而不是让引擎去查弹劾记录：
      引擎取裁判一律走 get_judge_sync，若再引一套 override 逻辑就有两个数据源，
      容易出现「弹劾记录说换了、引擎还用旧的」。改这张表 = 单一数据源，
      引擎下一轮自动拿到新裁判，详情接口与 SSE 里的裁判信息同步更新。
    """
    cfg = AI_MODELS.get(new_ai_id)
    if cfg is None:
        return False
    meta = _JUDGE_META.get(judge_type, {})
    # 席位名保留角色标识，再挂上模型名，界面上能直接看出换成了谁
    display_name = (
        f"{meta.get('display_name', cfg.display_name)}·{cfg.display_name}"
    )
    with get_transaction() as conn:
        cur = conn.execute(
            """
            UPDATE discussion_participants
               SET ai_id = ?, display_name = ?, channel = ?
             WHERE discussion_id = ? AND role = 'judge' AND judge_type = ?
            """,
            (new_ai_id, display_name, cfg.channel,
             discussion_id, judge_type),
        )
        return cur.rowcount > 0


# ------------------------------------------------------------
# 评分：round_scores
# ------------------------------------------------------------

def upsert_round_scores_sync(
    discussion_id: int, round_no: int,
    scores: List[Dict[str, Any]], judge_model: str,
) -> None:
    """
    批量写入某轮评分（宽表，UNIQUE(discussion_id, round, seat_id)）。
    scores: [{ai_id, display_name, seat_id, quality, ...}]
    seat_id 用于区分同源席位；历史数据可能为空。
    """
    with get_transaction() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO round_scores
                (discussion_id, round, ai_id, seat_id, display_name,
                 quality, relevance, contribution, compliance,
                 weighted_total, judge_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    discussion_id, round_no, s["ai_id"], s.get("seat_id"),
                    s["display_name"],
                    s["quality"], s["relevance"], s["contribution"],
                    s["compliance"], s["weighted_total"], judge_model,
                )
                for s in scores
            ],
        )


def get_round_scores_sync(discussion_id: int, round_no: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT ai_id, display_name, seat_id, quality, relevance, contribution,
                   compliance, weighted_total, judge_model
            FROM round_scores
            WHERE discussion_id = ? AND round = ?
            """,
            (discussion_id, round_no),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_round_scores_sync(discussion_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT round, ai_id, display_name, seat_id,
                   quality, relevance, contribution, compliance, weighted_total
            FROM round_scores
            WHERE discussion_id = ?
            ORDER BY round ASC, seat_id ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_leaderboard_sync(discussion_id: int) -> List[Dict[str, Any]]:
    """
    累计计分榜：按 participants.total_score 降序。
    含未发言/被禁言者（分数可能为 0）。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, ai_id, display_name, total_score
            FROM discussion_participants
            WHERE discussion_id = ? AND role = 'discussant'
            ORDER BY total_score DESC, id ASC
            """,
            (discussion_id,),
        ).fetchall()
    result = []
    for idx, r in enumerate(rows, start=1):
        result.append({
            "rank": idx,
            "ai_id": r["ai_id"],
            "display_name": r["display_name"],
            "total_score": round(float(r["total_score"] or 0.0), 1),
        })
    return result


def compute_rank_in_round_sync(
    discussion_id: int, round_no: int, ai_id: str, seat_id: Optional[int] = None,
) -> Tuple[Optional[int], int]:
    """
    计算某个席位在某轮的排名（1 = 最高）**与该轮实际计分人数**。

    返回 (rank, round_scored_count)：
      - rank：该席位当轮名次；该轮没有它的计分记录时为 None（缺席不算末位）
      - round_scored_count：该轮真正参与计分的人数

    必须把「当轮人数」一起返回：跨轮判定「连续末位」时，历史轮可能有缺席者
    （计分人数比当前轮少），若拿当前轮人数当分母，末位 rank 恒小于它，
    连续末位永远判不出来。

    seat_id 优先：同一模型占多个席位时，按 ai_id 会把同源席位的分数混在一起。
    """
    with get_connection() as conn:
        if seat_id is not None:
            # 有 seat_id 的记录才计入，避免把老数据（seat_id 为 NULL）的行混进来
            scope_sql = "seat_id IS NOT NULL"
            match_sql = "seat_id = ?"
            match_val: Any = seat_id
        else:
            scope_sql = "1 = 1"
            match_sql = "ai_id = ?"
            match_val = ai_id

        cnt_row = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM round_scores
            WHERE discussion_id = ? AND round = ? AND {scope_sql}
            """,
            (discussion_id, round_no),
        ).fetchone()
        count = int(cnt_row["c"]) if cnt_row else 0

        target = conn.execute(
            f"""
            SELECT weighted_total FROM round_scores
            WHERE discussion_id = ? AND round = ? AND {match_sql}
            """,
            (discussion_id, round_no, match_val),
        ).fetchone()
        if target is None:
            return None, count

        row = conn.execute(
            f"""
            SELECT COUNT(*) + 1 AS rank FROM round_scores
            WHERE discussion_id = ? AND round = ? AND {scope_sql}
              AND weighted_total > ?
            """,
            (discussion_id, round_no, target["weighted_total"]),
        ).fetchone()
    return (int(row["rank"]) if row else 1), count


def get_participant_count_sync(discussion_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM discussion_participants WHERE discussion_id = ? AND role='discussant'",
            (discussion_id,),
        ).fetchone()
    return int(row["c"]) if row else 0


# ============================================================
# 一AI多职：表结构迁移（幂等，服务启动时调用）
# ============================================================

def _rebuild_round_scores_for_seats(conn) -> None:
    """重建 round_scores：新增 seat_id，唯一约束改为 (discussion_id, round, seat_id)。"""
    conn.execute(
        """
        CREATE TABLE round_scores_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
            round           INTEGER NOT NULL,
            ai_id           TEXT NOT NULL,
            seat_id         INTEGER,
            display_name    TEXT NOT NULL,
            quality         REAL DEFAULT 0.0,
            relevance       REAL DEFAULT 0.0,
            contribution    REAL DEFAULT 0.0,
            compliance      REAL DEFAULT 0.0,
            weighted_total  REAL DEFAULT 0.0,
            judge_model     TEXT NOT NULL,
            scored_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(discussion_id, round, seat_id)
        )
        """
    )
    # 历史数据：用 display_name 反查席位 id（旧数据里席位唯一，能一对一匹配）
    conn.execute(
        """
        INSERT INTO round_scores_new
            (id, discussion_id, round, ai_id, seat_id, display_name,
             quality, relevance, contribution, compliance,
             weighted_total, judge_model, scored_at)
        SELECT rs.id, rs.discussion_id, rs.round, rs.ai_id, p.id, rs.display_name,
               rs.quality, rs.relevance, rs.contribution, rs.compliance,
               rs.weighted_total, rs.judge_model, rs.scored_at
        FROM round_scores rs
        LEFT JOIN discussion_participants p
               ON p.discussion_id = rs.discussion_id
              AND p.display_name  = rs.display_name
              AND p.role = 'discussant'
        """
    )
    conn.execute("DROP TABLE round_scores")
    conn.execute("ALTER TABLE round_scores_new RENAME TO round_scores")
    conn.execute(
        "CREATE INDEX idx_rs_disc_round ON round_scores(discussion_id, round)")
    conn.execute("CREATE INDEX idx_rs_ai ON round_scores(ai_id)")


def ensure_multi_seat_schema_sync() -> None:
    """
    放开「同一个 AI 占多个席位」所需的两个约束：
      1. discussion_participants 的 UNIQUE(discussion_id, ai_id, role)
         —— 它让重复席位无法入库（UNIQUE constraint failed）。
      2. round_scores 的 UNIQUE(discussion_id, round, ai_id)
         —— 它让同源席位的本轮评分互相覆盖，改为按 seat_id 唯一。
    """
    with get_transaction() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_dp_unique")

        has_seat = any(
            r[1] == "seat_id"
            for r in conn.execute("PRAGMA table_info(round_scores)").fetchall()
        )
        if not has_seat:
            _rebuild_round_scores_for_seats(conn)


# ------------------------------------------------------------
# 禁言：discussion_bans（事件流式）
# ------------------------------------------------------------

def ensure_ban_columns_sync() -> None:
    """
    补建 discussion_bans 的 seat_id 列（线上老库自动补齐，避免手改 DB）。

    没有这列之前，解除禁言只能按 ai_id 匹配，同一模型占两个席位、
    只禁其一的情况下，到期解禁会把另一个席位的禁言也一起清掉。
    """
    with get_transaction() as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(discussion_bans)").fetchall()}
        if "seat_id" not in cols:
            conn.execute("ALTER TABLE discussion_bans ADD COLUMN seat_id INTEGER")


def insert_ban_record_sync(
    discussion_id: int, ai_id: str, display_name: str,
    action: str, reason: str, ban_type: Optional[str],
    effective_round: int, ban_rounds: int = 1, is_active: bool = False,
    seat_id: Optional[int] = None,
) -> int:
    """
    写一条禁言 / 解禁记录。

    seat_id 记下席位是必需的：解禁时要按席位匹配，
    否则同一模型的另一席位会被连带解除。
    """
    with get_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO discussion_bans
                (discussion_id, ai_id, display_name, action, reason,
                 ban_type, effective_round, ban_rounds, is_active, seat_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (discussion_id, ai_id, display_name, action, reason,
             ban_type, effective_round, ban_rounds, 1 if is_active else 0,
             seat_id),
        )
        return cur.lastrowid


def deactivate_ban_sync(discussion_id: int, ai_id: str,
                        seat_id: Optional[int] = None) -> None:
    """
    把某 AI 当前 is_active=1 的 banned 记录置为 0（解除禁言时用）。

    seat_id 不为 None 时只解除该席位的记录——同一模型占多席位时，
    按 ai_id 解除会把另一个仍在禁言期的席位一起误清。
    历史记录没有 seat_id（NULL），这里一并放行，保证对老数据兼容。
    """
    with get_transaction() as conn:
        conn.execute(
            """
            UPDATE discussion_bans
            SET is_active = 0
            WHERE discussion_id = ? AND ai_id = ? AND action = 'banned' AND is_active = 1
              AND (seat_id IS NULL OR ? IS NULL OR seat_id = ?)
            """,
            (discussion_id, ai_id, seat_id, seat_id),
        )


def get_active_bans_sync(discussion_id: int) -> List[Dict[str, Any]]:
    """当前仍在生效的禁言（action='banned' AND is_active=1）。"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT ai_id, display_name, ban_type, effective_round, ban_rounds,
                   seat_id
            FROM discussion_bans
            WHERE discussion_id = ? AND action = 'banned' AND is_active = 1
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_ban_history_sync(discussion_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT ai_id, display_name, action, reason, ban_type,
                   effective_round, ban_rounds, is_active, created_at
            FROM discussion_bans
            WHERE discussion_id = ?
            ORDER BY id ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------
# 轮次总结 / 裁判日志 / 讨论收尾
# ------------------------------------------------------------

def insert_round_summary_sync(
    discussion_id: int, round_no: int,
    messages_count: int, duration_ms: int, summary_text: str,
) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO discussion_round_summaries
                (discussion_id, round, messages_count, duration_ms, summary_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (discussion_id, round_no, messages_count, duration_ms, summary_text),
        )


def get_round_summaries_sync(discussion_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT round, messages_count, duration_ms, summary_text
            FROM discussion_round_summaries
            WHERE discussion_id = ?
            ORDER BY round ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_judge_log_sync(
    discussion_id: int, judge_type: str, judge_model: str,
    action_type: str, target_ai_id: Optional[str] = None,
    detail: Optional[str] = None, round_no: Optional[int] = None,
) -> None:
    with get_transaction() as conn:
        conn.execute(
            """
            INSERT INTO discussion_judge_logs
                (discussion_id, judge_type, judge_model, action_type,
                 target_ai_id, detail, round)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (discussion_id, judge_type, judge_model, action_type,
             target_ai_id, detail, round_no),
        )


def update_discussion_judge_summary_sync(discussion_id: int, judge_summary: str) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET judge_summary = ? WHERE id = ?",
            (judge_summary, discussion_id),
        )


def update_discussion_action_plan_sync(discussion_id: int, action_plan: str) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET action_plan = ? WHERE id = ?",
            (action_plan, discussion_id),
        )


def update_discussion_total_duration_sync(discussion_id: int, total_duration_ms: int) -> None:
    with get_transaction() as conn:
        conn.execute(
            "UPDATE discussions SET total_duration_ms = ? WHERE id = ?",
            (total_duration_ms, discussion_id),
        )


def set_final_rank_sync(discussion_id: int) -> None:
    """根据 total_score 写入 final_rank。"""
    with get_transaction() as conn:
        rows = conn.execute(
            """
            SELECT id FROM discussion_participants
            WHERE discussion_id = ? AND role = 'discussant'
            ORDER BY total_score DESC, id ASC
            """,
            (discussion_id,),
        ).fetchall()
        for rank, r in enumerate(rows, start=1):
            conn.execute(
                "UPDATE discussion_participants SET final_rank = ? WHERE id = ?",
                (rank, r["id"]),
            )
