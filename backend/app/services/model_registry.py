# ============================================================
# 文件：backend/app/services/model_registry.py
# 说明：将 settings.AI_MODELS 同步到 ai_model_registry 表。
#       init.sql 中的模型数据仅为兜底，真相源是 .env / settings.py。
# 约束：使用同步 sqlite3 + 手动事务（与 connection.py 一致）。
# ============================================================

from __future__ import annotations

import logging

from app.config import AI_MODELS
from app.database.connection import get_transaction

logger = logging.getLogger("app.model_registry")


_UPSERT_SQL = """
    INSERT INTO ai_model_registry
        (ai_id, display_name, channel, model_name,
         max_tokens, timeout_seconds, is_active)
    VALUES (?, ?, ?, ?, ?, ?, 1)
    ON CONFLICT(ai_id) DO UPDATE SET
        display_name    = excluded.display_name,
        channel         = excluded.channel,
        model_name      = excluded.model_name,
        max_tokens      = excluded.max_tokens,
        timeout_seconds = excluded.timeout_seconds
"""


def sync_model_registry() -> int:
    """
    将 settings.AI_MODELS 全量同步到 ai_model_registry。
    - 已存在 ai_id → 更新字段（模型名、超时等以 .env 为准）
    - 不存在 ai_id → 插入

    返回同步的记录数。
    """
    count = 0
    with get_transaction() as conn:
        for cfg in AI_MODELS.values():
            conn.execute(
                _UPSERT_SQL,
                (
                    cfg.ai_id,
                    cfg.display_name,
                    cfg.channel,
                    cfg.model_name,
                    cfg.max_tokens,
                    cfg.timeout_seconds,
                ),
            )
            count += 1
    logger.info("模型注册表已同步 %d 条记录", count)
    return count
