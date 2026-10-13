"""轻量文档解析：PDF / Word / TXT / Markdown，超长自动截断。"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from app.config import settings


def _extract_sync(filename: str, raw: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower().lstrip(".")
    kind = ext or "txt"
    text = ""
    try:
        if ext == "pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            pages = [p.extract_text() or "" for p in reader.pages[:30]]
            text = "\n".join(pages)
        elif ext in {"docx", "doc"}:
            if ext == "docx":
                from docx import Document

                doc = Document(io.BytesIO(raw))
                text = "\n".join(p.text for p in doc.paragraphs)
            else:
                text = raw.decode("utf-8", errors="ignore")
        elif ext in {"txt", "md", "markdown", "csv", "json", "log"}:
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = raw.decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        text = f"[文档解析失败：{type(exc).__name__}，已降级为纯文本读取]"
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return text, kind


async def extract_text(filename: str, raw: bytes) -> tuple[str, str]:
    """返回 (文本内容, 文档类型)。超长文档仅保留前 N 字，避免内存溢出。"""
    text, kind = await asyncio.to_thread(_extract_sync, filename, raw)
    if len(text) > settings.max_doc_chars:
        text = text[: settings.max_doc_chars] + "\n…（超长文档已自动截断）"
    return text, kind
