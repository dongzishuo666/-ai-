# ============================================================
# 文件：backend/app/services/sse_manager.py
# 说明：单进程 SSE 连接管理器（1 worker 模式下内存广播）。
# 约束：禁止引入跨进程通信（Redis / 落库轮询）—— 见补充裁定（一）。
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("app.sse_manager")


# 队列容量（事件积压上限，溢出时丢弃当前事件，不阻塞任务）
_QUEUE_MAXSIZE = 100


class SSEManager:
    """
    单进程 SSE 连接管理器。
    - key: stream_id（如 'article_1'）
    - value: asyncio.Queue
    """

    def __init__(self) -> None:
        self._connections: Dict[str, asyncio.Queue] = {}
        self._cancelled: set = set()  # 已请求取消的 stream_id

    # ---------- 注册 / 注销 / 查询 ----------

    def register(self, stream_id: str) -> asyncio.Queue:
        """注册新连接，返回专属队列。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._connections[stream_id] = queue
        logger.debug("SSE 注册: %s", stream_id)
        return queue

    def unregister(self, stream_id: str) -> None:
        """注销连接（幂等）。"""
        if self._connections.pop(stream_id, None) is not None:
            logger.debug("SSE 注销: %s", stream_id)

    def is_registered(self, stream_id: str) -> bool:
        return stream_id in self._connections

    # ---------- 取消控制 ----------

    def cancel(self, stream_id: str) -> bool:
        """请求取消某流。返回是否真的登记了取消（流存在才生效）。"""
        if stream_id not in self._connections:
            return False
        self._cancelled.add(stream_id)
        logger.info("SSE 取消请求: %s", stream_id)
        return True

    def is_cancelled(self, stream_id: str) -> bool:
        return stream_id in self._cancelled

    def clear_cancel(self, stream_id: str) -> None:
        """清理取消标记（流结束后调用）。"""
        self._cancelled.discard(stream_id)

    def get_queue(self, stream_id: str) -> Optional[asyncio.Queue]:
        return self._connections.get(stream_id)

    # ---------- 推送 ----------

    async def publish(self, stream_id: str, event: str, data: Any) -> bool:
        """
        向指定流推送事件。
        返回：
            True  → 推送成功
            False → 无活跃连接（静默丢弃）或队列满（丢弃当前事件）
        """
        queue = self._connections.get(stream_id)
        if queue is None:
            return False
        try:
            queue.put_nowait({"event": event, "data": data})
            return True
        except asyncio.QueueFull:
            logger.warning("SSE 队列已满，丢弃事件 %s (stream=%s)", event, stream_id)
            return False


# 全局单例
sse_manager = SSEManager()
