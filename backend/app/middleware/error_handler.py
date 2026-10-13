# ============================================================
# 文件：backend/app/middleware/error_handler.py
# 说明：全局异常处理。统一捕获业务异常、参数校验异常、
#       HTTP 异常与未捕获异常，统一格式化响应。
# ============================================================

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.error")


class AppException(Exception):
    """
    业务异常基类。
    错误码常量请参考施工蓝图 V1.2 第 1 部分 0.4 节。
    """

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        detail: Optional[str] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 实例上注册全局异常处理器。"""

    @app.exception_handler(AppException)
    async def _handle_app_exception(_: Request, exc: AppException) -> JSONResponse:
        body = {
            "code": exc.http_status,
            "error": exc.code,
            "message": exc.message,
        }
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(status_code=exc.http_status, content=body)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "code": 400,
                "error": "VALIDATION_ERROR",
                "message": "参数不合法",
                "detail": str(exc.errors()),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code,
                "error": "HTTP_ERROR",
                "message": str(exc.detail),
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unhandled(_: Request, exc: Exception) -> JSONResponse:
        # 记录完整堆栈，但响应中不暴露内部细节
        logger.exception("未捕获异常：%s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "error": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )
