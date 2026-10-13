"""SSE 任务通道。

设计说明：
- 因为浏览器 ``EventSource`` 只能发 GET 请求，所以三大功能统一采用
  「POST 创建任务 → 返回 task_id → GET /stream 订阅」两段式流程；
- 后台任务把事件写入 ``asyncio.Queue``，SSE 端点从队列消费并下发；
- 单进程单 worker 场景下内存占用极小，并带自动过期清理。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncIterator

TASK_TTL = 30 * 60  # 任务通道保留 30 分钟


class TaskChannel:
    """单个任务的事件通道。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.created_at = time.time()

    async def emit(self, event_type: str, **payload) -> None:
        if self.closed:
            return
        await self.queue.put({"type": event_type, **payload})

    async def finish(self, event_type: str = "done", **payload) -> None:
        await self.emit(event_type, **payload)
        self.closed = True
        await self.queue.put(None)

    async def fail(self, message: str) -> None:
        await self.emit("error", message=message)
        self.closed = True
        await self.queue.put(None)

    async def stream(self) -> AsyncIterator[dict]:
        while True:
            item = await self.queue.get()
            if item is None:
                break
            yield item


class TaskHub:
    def __init__(self) -> None:
        self._channels: dict[str, TaskChannel] = {}

    def create(self) -> TaskChannel:
        self._sweep()
        task_id = uuid.uuid4().hex
        channel = TaskChannel(task_id)
        self._channels[task_id] = channel
        return channel

    def get(self, task_id: str) -> TaskChannel | None:
        return self._channels.get(task_id)

    def discard(self, task_id: str) -> None:
        self._channels.pop(task_id, None)

    def _sweep(self) -> None:
        deadline = time.time() - TASK_TTL
        stale = [tid for tid, ch in self._channels.items() if ch.created_at < deadline]
        for tid in stale:
            self._channels.pop(tid, None)


hub = TaskHub()


def sse_format(event: dict) -> str:
    """把事件序列化为 SSE 文本帧。"""
    import json

    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
