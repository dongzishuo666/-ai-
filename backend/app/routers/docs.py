# ============================================================
# 文件：backend/app/routers/docs.py
# 说明：功能一（文档成品生成）路由。产物是按用户需求完成的成品，不是 Prompt。
# 状态：批次1 → 实现 POST /upload
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, File, UploadFile

from app.config import MAX_FILE_SIZE_MB, UPLOAD_DIR
from app.database import doc_repo
from app.middleware.error_handler import AppException
from app.models.schemas import ApiResponse, DocUploadData
from app.services.doc_parser import (
    SUPPORTED_EXTENSIONS,
    DocParseError,
    detect_doc_type,
    parse_document,
)
from app.utils.text_utils import count_chars, truncate_text

logger = logging.getLogger("app.routers.docs")

router = APIRouter(prefix="/api/docs", tags=["docs"])


# 文件大小上限（字节）
_MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# 可自选模型的角色（功能一三棒）
_DOC_ROLE_KEYS = ("planner", "optimizer", "finalizer")


def _roles_query(roles: Optional[Dict[str, str]], keys) -> str:
    """
    把自选模型拼成 stream_url 查询串。

    自选结果不落库——流水线由 SSE 连接触发，角色随连接参数走即可，
    避免为一次运行给表加列、做迁移。
    """
    if not roles:
        return ""
    parts = [
        f"{k}={roles[k]}" for k in keys
        if isinstance(roles.get(k), str) and roles[k].strip()
    ]
    return "?" + "&".join(parts) if parts else ""


def _safe_filename(name: str) -> str:
    """去除路径分隔符与危险字符，仅保留 basename。"""
    base = os.path.basename(name or "unnamed")
    base = base.replace("\\", "_").replace("/", "_")
    return base or "unnamed"


@router.post("/upload", response_model=ApiResponse)
async def upload_document(file: UploadFile = File(...)) -> ApiResponse:
    """
    上传文档并解析。
    - 校验格式（pdf/docx/txt/md）
    - 校验大小（≤ MAX_FILE_SIZE_MB）
    - 校验非空
    - 解析纯文本 → 智能截断至 8000 字符 → 落库（status='pending'）
    - 文件以唯一名写入 UPLOAD_DIR
    """
    filename = file.filename or ""

    # 1. 格式识别
    try:
        doc_type = detect_doc_type(filename)
    except DocParseError as e:
        raise AppException(
            code="UNSUPPORTED_FORMAT",
            message="仅支持 PDF/DOCX/TXT/MD 格式",
            http_status=415,
            detail=str(e),
        )

    # 2. 读取（限制大小）
    content = await file.read()
    file_size = len(content)

    if file_size == 0:
        raise AppException(
            code="EMPTY_FILE",
            message="文件内容为空，请检查",
            http_status=400,
        )
    if file_size > _MAX_FILE_BYTES:
        raise AppException(
            code="FILE_TOO_LARGE",
            message=f"文件大小不能超过{MAX_FILE_SIZE_MB}MB",
            http_status=413,
        )

    # 3. 解析（同步耗时操作，to_thread）
    try:
        raw_text = await asyncio.to_thread(parse_document, content, doc_type)
    except DocParseError as e:
        raise AppException(
            code="PARSE_FAILED",
            message="文档解析失败，请确认文件未损坏",
            http_status=500,
            detail=str(e),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("文档解析未捕获异常")
        raise AppException(
            code="PARSE_FAILED",
            message="文档解析失败，请确认文件未损坏",
            http_status=500,
            detail=str(e),
        )

    if not raw_text.strip():
        raise AppException(
            code="EMPTY_FILE",
            message="文档解析后内容为空，请检查文件",
            http_status=400,
        )

    # 4. 智能截断 + 字符统计
    truncated = truncate_text(raw_text)
    char_count = count_chars(truncated)

    # 5. 落盘（唯一文件名）
    safe_name = _safe_filename(filename)
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    dest = UPLOAD_DIR / unique_name
    try:
        await asyncio.to_thread(dest.write_bytes, content)
    except Exception as e:  # noqa: BLE001
        logger.exception("文件写盘失败")
        raise AppException(
            code="INTERNAL_ERROR",
            message="文件保存失败，请稍后重试",
            http_status=500,
            detail=str(e),
        )

    # 6. 入库
    try:
        row = await asyncio.to_thread(
            doc_repo.insert_doc_task_sync,
            filename=safe_name,
            doc_type=doc_type,
            file_size=file_size,
            file_path=str(dest),
            char_count=char_count,
            raw_text=truncated,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("文档任务入库失败")
        # 尽力清理已落盘文件
        try:
            await asyncio.to_thread(dest.unlink, True)
        except Exception:  # noqa: BLE001
            pass
        raise AppException(
            code="INTERNAL_ERROR",
            message="任务创建失败，请稍后重试",
            http_status=500,
            detail=str(e),
        )

    data = DocUploadData(
        task_id=row["task_id"],
        filename=safe_name,
        doc_type=doc_type,
        file_size=file_size,
        char_count=char_count,
        status=row["status"],
        created_at=row["created_at"],
    )
    logger.info(
        "文档上传成功：task_id=%s type=%s size=%d chars=%d",
        row["task_id"], doc_type, file_size, char_count,
    )
    return ApiResponse(data=data.model_dump())


import json
from typing import AsyncIterator

from fastapi.responses import StreamingResponse

from app.config import SSE_HEARTBEAT_INTERVAL
from app.models.schemas import DocProcessData, DocProcessRequest
from app.services.doc_pipeline import run_doc_pipeline
from app.services.sse_manager import sse_manager


# ============================================================
# 追加：批次2 · process 接口 + SSE 流
# ============================================================

# 后台任务引用集（防止 GC）
_bg_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ---------- 响应头 ----------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------- process ----------

@router.post("/process", response_model=ApiResponse)
async def process_document(payload: DocProcessRequest) -> ApiResponse:
    """
    提交处理请求。
    裁定三：仅入库 user_need，不启动任务；任务由 SSE 连接后启动。
    """
    task = await asyncio.to_thread(doc_repo.get_doc_task_sync, payload.task_id)
    if task is None:
        raise AppException(
            code="NOT_FOUND",
            message="任务不存在",
            http_status=404,
        )

    # 仅 pending 可提交
    if task["status"] != "pending":
        raise AppException(
            code="TASK_BUSY",
            message=f"任务当前状态为 {task['status']}，无法提交处理",
            http_status=409,
        )

    # 写入 user_need
    await asyncio.to_thread(
        doc_repo.update_doc_task_user_need_sync,
        payload.task_id, payload.user_need,
    )

    stream_url = (
        f"/api/docs/{payload.task_id}/stream"
        + _roles_query(payload.roles, _DOC_ROLE_KEYS)
    )
    data = DocProcessData(
        task_id=payload.task_id,
        status="pending",
        stream_url=stream_url,
    )
    logger.info("文档处理请求已接受：task_id=%s", payload.task_id)
    return ApiResponse(data=data.model_dump())


# ---------- SSE 生成器 ----------

async def _sse_generator(queue: asyncio.Queue, stream_id: str) -> AsyncIterator[str]:
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=SSE_HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            event = item.get("event")
            data = item.get("data", {})

            if event == "__close__":
                return

            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            if event == "done":
                return
    finally:
        sse_manager.unregister(stream_id)


async def _sse_terminal_doc(task_id: int, status: str) -> AsyncIterator[str]:
    """
    终态连接：
    - done/saved → 读 final_prompt 推 final_result + done
    - failed     → 读 error_message 推 error + done
    """
    if status in ("done", "saved"):
        task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
        payload = {
            # prompt 字段保留以兼容旧前端；成品语义字段名是 result
            "prompt": (task or {}).get("final_prompt", "") or "",
            "result": (task or {}).get("final_prompt", "") or "",
            "total_duration_ms": (task or {}).get("total_duration_ms", 0),
            "token_usage": {"input": 0, "output": 0, "total": 0},
        }
        yield f"event: final_result\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"
    else:
        task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
        msg = (task or {}).get("error_message", "") or "任务已失败"
        yield f"event: error\ndata: {json.dumps({'message': msg, 'code': 'INTERNAL_ERROR'}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"


# ---------- stream ----------

@router.get("/{task_id}/stream")
async def stream_document(
    task_id: int,
    planner: Optional[str] = None,
    optimizer: Optional[str] = None,
    finalizer: Optional[str] = None,
):
    """
    SSE 进度流。裁定三：连接后启动任务。
    可选查询参数 planner / optimizer / finalizer：指定各棒使用的模型。
    - pending     → 注册 → 原子切 running → 启动流水线
    - running     → 重连（不重启）
    - done/saved/failed → 走终态分支
    - 已有活跃连接 → 409
    """
    task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
    if task is None:
        raise AppException(
            code="NOT_FOUND", message="任务不存在", http_status=404,
        )

    status = task["status"]

    # 终态直接返回
    if status in ("done", "saved", "failed"):
        return StreamingResponse(
            _sse_terminal_doc(task_id, status),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    stream_id = f"doc_{task_id}"

    # 重复连接防护
    if sse_manager.is_registered(stream_id):
        raise AppException(
            code="TASK_BUSY",
            message="该任务已有活跃的 SSE 连接",
            http_status=409,
        )

    # 先注册，再启动（消除竞态）
    queue = sse_manager.register(stream_id)

    # 首次连接：原子切状态并启动流水线
    if status == "pending":
        ok = await asyncio.to_thread(
            doc_repo.update_doc_task_status_sync,
            task_id, "running", expected_status="pending",
        )
        if not ok:
            sse_manager.unregister(stream_id)
            raise AppException(
                code="TASK_BUSY",
                message="任务已被其他连接启动",
                http_status=409,
            )
        roles = {
            "planner": planner, "optimizer": optimizer, "finalizer": finalizer
        }
        _spawn(run_doc_pipeline(task_id, roles or None))

    return StreamingResponse(
        _sse_generator(queue, stream_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


from fastapi import APIRouter, File, Query, UploadFile


# ============================================================
# 追加：批次3 · 详情 / 保存 / 列表 / 删除
# ============================================================

# ---------- 辅助：组装 steps 数组 ----------

def _build_steps_list(step_rows: list) -> list:
    """
    将 repo 返回的 doc_steps 列表转为蓝图 1.4 约定的数组结构。
    - 保留 step_number / role / model / status / duration_ms / output_preview
    - failed / skipped 时附 error_message
    """
    result = []
    for s in step_rows:
        item = {
            "step": s["step_number"],
            "role": s["role"],
            "model": s["model"],
            "status": s["status"],
            "duration_ms": s["duration_ms"],
            "output_preview": s["output_preview"],
        }
        if s["status"] in ("failed", "skipped") and s["error_message"]:
            item["error_message"] = s["error_message"]
        result.append(item)
    return result


# ---------- 详情 ----------

@router.get("/{task_id}", response_model=ApiResponse)
async def get_doc_detail(task_id: int) -> ApiResponse:
    """
    获取文档任务详情（含三步骤摘要）。
    不存在返回 404 NOT_FOUND。
    """
    task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
    if task is None:
        raise AppException(
            code="NOT_FOUND",
            message="任务不存在",
            http_status=404,
        )

    steps = await asyncio.to_thread(doc_repo.get_doc_steps_sync, task_id)

    data = {
        "task_id": task["task_id"],
        "filename": task["filename"],
        "doc_type": task["doc_type"],
        "file_size": task["file_size"],
        "char_count": task["char_count"],
        "summary": task["summary"],
        "user_need": task["user_need"],
        "final_prompt": task["final_prompt"],
        "status": task["status"],
        "error_message": task["error_message"],
        "total_duration_ms": task["total_duration_ms"],
        "steps": _build_steps_list(steps),
        "created_at": task["created_at"],
        "completed_at": task["completed_at"],
    }
    return ApiResponse(data=data)


# ---------- 保存 ----------

@router.post("/{task_id}/save", response_model=ApiResponse)
async def save_doc_result(task_id: int) -> ApiResponse:
    """
    标记任务为已保存。
    - 仅允许 done → saved（幂等：saved 重复调用直接返回成功）
    - 其他状态（pending/running/failed）→ 409 TASK_BUSY
    """
    task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
    if task is None:
        raise AppException(
            code="NOT_FOUND",
            message="任务不存在",
            http_status=404,
        )

    status = task["status"]

    # 幂等：已保存直接返回
    if status == "saved":
        return ApiResponse(
            data={"task_id": task_id, "status": "saved"},
            message="ok",
        )

    # 仅 done 可保存
    if status != "done":
        raise AppException(
            code="TASK_BUSY",
            message=f"任务状态为 {status}，仅 done 状态可保存",
            http_status=409,
        )

    ok = await asyncio.to_thread(
        doc_repo.update_doc_task_status_sync,
        task_id, "saved", expected_status="done",
    )
    if not ok:
        # 理论上不会触发（前面已查状态），保留并发兜底
        raise AppException(
            code="TASK_BUSY",
            message="保存失败，任务状态可能已变更",
            http_status=409,
        )

    logger.info("文档任务已保存：task_id=%s", task_id)
    return ApiResponse(data={"task_id": task_id, "status": "saved"})


# ---------- 列表 ----------

@router.get("", response_model=ApiResponse)
async def list_docs(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(20, ge=1, le=50, description="每页条数，最大50"),
) -> ApiResponse:
    """
    文档任务历史列表（分页）。
    默认 page=1、page_size=20；page_size 上限 50（与蓝图 0.8 一致）。
    """
    result = await asyncio.to_thread(
        doc_repo.list_doc_tasks_sync, page, page_size
    )
    return ApiResponse(data=result)


# ---------- 删除 ----------

@router.delete("/{task_id}", response_model=ApiResponse)
async def delete_doc(task_id: int) -> ApiResponse:
    """
    删除文档任务（含 steps，靠 CASCADE）。
    约束：仅允许 done / saved / failed；其他状态返回 409 TASK_BUSY。
    """
    task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
    if task is None:
        raise AppException(
            code="NOT_FOUND",
            message="任务不存在",
            http_status=404,
        )

    if task["status"] not in ("done", "saved", "failed"):
        raise AppException(
            code="TASK_BUSY",
            message=f"任务状态为 {task['status']}，仅允许删除 done/saved/failed 状态的任务",
            http_status=409,
        )

    deleted = await asyncio.to_thread(doc_repo.delete_doc_task_sync, task_id)
    if not deleted:
        raise AppException(
            code="TASK_BUSY",
            message="删除失败，任务状态可能已变更",
            http_status=409,
        )

    # 尽力清理落盘文件（失败不阻塞响应）
    file_path = task.get("file_path")
    if file_path:
        try:
            await asyncio.to_thread(os.unlink, file_path)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("清理落盘文件失败：%s (%s)", file_path, e)

    logger.info("文档任务已删除：task_id=%s", task_id)
    return ApiResponse(data={"task_id": task_id, "deleted": True})


# ============================================================
# 追加：成品导出（Word / Markdown）
# 说明：final_prompt 字段存的是三棒跑完的成品正文，这里把它落成文件。
# ============================================================

import io
from urllib.parse import quote

from fastapi.responses import StreamingResponse

from app.services import doc_export


def _export_basename(filename: str, task_id: int) -> str:
    """导出文件名：原文件名（去扩展名）+ 成品 + 任务号。"""
    stem = os.path.splitext(_safe_filename(filename or ""))[0].strip()
    stem = "".join(ch for ch in stem if ch not in '\\/:*?"<>|')[:40]
    return f"{stem or '文档'}-成品-{task_id}"


@router.get("/{task_id}/export")
async def export_doc(task_id: int, format: str = "docx"):
    """
    导出成品文件。
    - format=docx → 三棒成品转成 Word（Markdown 结构映射）
    - format=md   → 原样返回 Markdown 文本
    """
    fmt = (format or "docx").strip().lower()
    if fmt not in ("docx", "md"):
        raise AppException(
            code="BAD_PARAM",
            message="导出格式仅支持 docx / md",
            http_status=400,
        )

    task = await asyncio.to_thread(doc_repo.get_doc_task_sync, task_id)
    if task is None:
        raise AppException(
            code="NOT_FOUND", message="任务不存在", http_status=404,
        )

    content = (task.get("final_prompt") or "").strip()
    if not content:
        raise AppException(
            code="TASK_BUSY",
            message=f"任务状态为 {task['status']}，暂无可导出的成品",
            http_status=409,
        )

    base = _export_basename(task.get("filename", ""), task_id)

    if fmt == "md":
        data = content.encode("utf-8")
        filename = f"{base}.md"
        media_type = "text/markdown; charset=utf-8"
    else:
        # docx 生成是 CPU 密集的同步操作，丢到线程里避免阻塞事件循环
        buffer = await asyncio.to_thread(
            doc_export.markdown_to_docx, content, base
        )
        data = buffer.getvalue()
        filename = f"{base}.docx"
        media_type = (
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        )

    # 中文文件名走 RFC 5987；同时给一个 ASCII 兜底名给老浏览器
    ascii_name = f"document-{task_id}.{fmt}"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        ),
        "Cache-Control": "no-store",
    }

    logger.info("文档成品导出：task_id=%s format=%s", task_id, fmt)
    return StreamingResponse(io.BytesIO(data), media_type=media_type, headers=headers)
