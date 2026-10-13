"""路由公共工具：SSE 响应与后台任务启动。"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi.responses import StreamingResponse

from app.core.tasks import TaskChannel, sse_format


def sse_response(channel: TaskChannel) -> StreamingResponse:
    """把任务通道包装成 SSE 响应。

    关键响应头 ``X-Accel-Buffering: no`` 用于关闭 Nginx 缓冲，
    否则流式输出会退化成「一次性返回」。
    """

    async def event_generator():
        try:
            async for event in channel.stream():
                yield sse_format(event)
        finally:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def launch(factory: Callable[[TaskChannel], Awaitable[dict]], channel: TaskChannel) -> None:
    """在事件循环中启动后台工作流，异常自动回传为 error 事件。"""

    async def runner() -> None:
        try:
            await factory(channel)
        except Exception as exc:  # noqa: BLE001
            await channel.fail(f"{type(exc).__name__}: {exc}")

    asyncio.create_task(runner())
