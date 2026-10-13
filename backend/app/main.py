# ============================================================
# 文件：backend/app/main.py
# 说明：FastAPI 应用入口。仅做装配，不含任何业务逻辑。
# 启动流程：ensure_directories → validate_config → init_database → sync_model_registry
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import LOG_LEVEL, ensure_directories, validate_config
from app.database.connection import init_database
from app.middleware.error_handler import register_exception_handlers
from app.routers import articles, discussions, docs, extensions, memory, scores, system
from app.services.ai_scheduler import close_http_client
from app.services.model_registry import sync_model_registry


# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")


# ============================================================
# 生命周期
# ============================================================

_SERVICE_HEARTBEAT_INTERVAL = 30     # 秒


async def _service_heartbeat(run_id: int) -> None:
    """
    定期写运行心跳。

    崩溃 / kill -9 时来不及写 shutdown_at，靠心跳把这一段截断，
    否则「已下线的时间」会被算进累计在线时长。
    """
    from app.database import service_run_repo
    while True:
        await asyncio.sleep(_SERVICE_HEARTBEAT_INTERVAL)
        try:
            await asyncio.to_thread(service_run_repo.heartbeat_sync, run_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("运行心跳写入失败: %s", e)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("=== 应用启动 ===")

    # 1) 目录准备（幂等）
    ensure_directories()

    # 2) 配置自检（不阻断启动，仅打印警告）
    for warning in validate_config():
        logger.warning("配置警告：%s", warning)

    # 3) 数据库初始化（app.db 不存在时自动执行 init.sql）
    is_new = init_database()
    logger.info("数据库初始化：%s", "首次建表" if is_new else "已存在")

    # 4) 同步模型注册表（以 settings.AI_MODELS 为真相源）
    sync_model_registry()

    # 4.2) 补建 chat_sessions 新增列（会话级模型选择）
    from app.database import extension_repo
    extension_repo.ensure_chat_session_columns_sync()

    # 4.3) 补建统一评分表（最终积分榜 / 质检分），老库自动补齐
    from app.database import score_repo
    score_repo.ensure_tables_sync()

    # 4.3) 补建 discussions 新增列（局中总结）
    from app.database import discussion_repo
    discussion_repo.ensure_discussion_columns_sync()

    # 4.4) discussion_bans 补 seat_id 列（禁言/解禁按席位对称）
    discussion_repo.ensure_ban_columns_sync()

    # 4.4b) end_reason 支持 pause_timeout（暂停超时不再记成用户手动终止）
    discussion_repo.ensure_end_reason_check_sync()

    # 4.5) 重置僵尸文章任务（提醒1）
    from app.database import article_repo
    reset_count = await asyncio.to_thread(article_repo.reset_stale_articles_sync)
    if reset_count > 0:
        logger.warning("启动重置僵尸文章任务：%d 条", reset_count)

    # 4.6) 重置僵尸文档任务
    from app.database import doc_repo
    doc_reset = await asyncio.to_thread(doc_repo.reset_stale_doc_tasks_sync)
    if doc_reset > 0:
        logger.warning("启动重置僵尸文档任务：%d 条", doc_reset)

    # 4.7) 重置僵尸讨论任务
    from app.database import discussion_repo
    disc_reset = await asyncio.to_thread(discussion_repo.reset_stale_discussions_sync)
    if disc_reset > 0:
        logger.warning("启动重置僵尸讨论任务：%d 条", disc_reset)

    # 4.8) 补建记忆库表：init.sql 仅在首次建库执行，老库升级不会自动带上新表
    from app.database import memory_repo
    await asyncio.to_thread(memory_repo.ensure_table_sync)

    # 4.9) 一AI多职迁移：放开「同一模型占多个席位」的两个唯一约束
    from app.database import discussion_repo
    await asyncio.to_thread(discussion_repo.ensure_multi_seat_schema_sync)

    # 4.10) 记录本次运行：/health 的 uptime_seconds 只反映当前进程（重启归零），
    #       累计运行时长需要把每次启动记成一段再相加
    from app.database import service_run_repo
    await asyncio.to_thread(service_run_repo.ensure_table_sync)
    run_id = await asyncio.to_thread(service_run_repo.start_run_sync)
    heartbeat_task = asyncio.create_task(_service_heartbeat(run_id))

    logger.info("=== 启动完成 ===")
    try:
        yield
    finally:
        heartbeat_task.cancel()
        await asyncio.to_thread(service_run_repo.end_run_sync, run_id)
        # 5) 释放 httpx 连接池
        await close_http_client()
        logger.info("=== 应用关闭 ===")


# ============================================================
# CORS 配置
# ============================================================

def _load_cors_origins() -> list[str]:
    """
    从环境变量 CORS_ORIGINS 读取允许来源（逗号分隔）。
    默认允许开发环境前端地址；生产环境请在 .env 追加服务器 IP。
    """
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


# ============================================================
# FastAPI 实例
# ============================================================

app = FastAPI(
    title="多AI协同工作平台",
    version="1.2.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=_load_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局异常处理
register_exception_handlers(app)

# 路由装配
app.include_router(system.router)
app.include_router(docs.router)
app.include_router(discussions.router)
app.include_router(articles.router)
app.include_router(extensions.router)
app.include_router(scores.router)
app.include_router(memory.router)
