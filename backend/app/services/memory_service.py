# ============================================================
# 文件：backend/app/services/memory_service.py
# 说明：记忆库业务层
#   - 把裁判总结 / 讨论者总结按结构化四节解析后写入 memories 表
#   - 解析失败不阻断流程：sections 为空时前端按纯文本展示
#
# 结构化解析的意义：裁判总结是 Markdown 长文，直接存文本无法做可视化。
# 这里把它切成「最终结论 / 各方贡献评估 / 争议点裁决 / 后续建议」四节，
# 前端据此渲染成四张分区卡片，实现真正的结构化可视化与检索。
# ============================================================

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from app.database import memory_repo

logger = logging.getLogger("app.services.memory_service")


# 裁判总结四节：顺序即展示顺序
JUDGE_SECTIONS: List[Dict[str, str]] = [
    {"key": "conclusion", "title": "最终结论"},
    {"key": "contributions", "title": "各方贡献评估"},
    {"key": "disputes", "title": "争议点裁决"},
    {"key": "suggestions", "title": "后续建议"},
]

# 标题别名 → 标准 key（兼容模型输出的各种写法）
_TITLE_ALIASES: Dict[str, str] = {
    "最终结论": "conclusion",
    "结论": "conclusion",
    "各方贡献评估": "contributions",
    "贡献评估": "contributions",
    "各方贡献": "contributions",
    "争议点裁决": "disputes",
    "争议裁决": "disputes",
    "争议点": "disputes",
    "后续建议": "suggestions",
    "建议": "suggestions",
}


def _normalize_heading(line: str) -> str:
    """
    把一行文本规范化成可能的标题形态：
    去掉 Markdown 标题符、序号前缀、粗体标记与尾随冒号。
    例："## 1. 最终结论：" → "最终结论"
    """
    s = line.strip()
    s = s.lstrip("#").strip()
    s = s.strip("*").strip()
    s = re.sub(r"^\d+[.、)）]\s*", "", s)
    s = re.sub(r"^[-*+]\s*", "", s)
    s = s.strip("*").strip()
    s = s.rstrip("：:").strip()
    return s


def parse_judge_sections(text: str) -> Dict[str, str]:
    """
    将裁判总结 Markdown 切分为四节。

    兼容写法：`## 1. 最终结论`、`### 最终结论`、`**1. 最终结论**`、`1. 最终结论`
    解析不出任何分节时返回空 dict，调用方按纯文本处理。
    """
    if not text or not text.strip():
        return {}

    sections: Dict[str, str] = {}
    current_key: Optional[str] = None
    buf: List[str] = []

    def flush() -> None:
        if current_key and buf:
            body = "\n".join(buf).strip()
            if body:
                sections[current_key] = body
        buf.clear()

    for raw_line in text.splitlines():
        heading = _normalize_heading(raw_line)

        # 标题行判定：规范化后必须命中别名，且不能是长句（避免正文被误判）
        matched: Optional[str] = None
        if heading and len(heading) <= 12:
            matched = _TITLE_ALIASES.get(heading)

        if matched:
            flush()
            current_key = matched
            # 同一行标题后可能直接跟了内容（如 "## 最终结论：xxx"）
            rest = raw_line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if rest and _normalize_heading(rest) != heading:
                buf.append(rest)
        elif current_key:
            buf.append(raw_line)

    flush()
    return sections


async def save_judge_summary_memory(
    discussion: Dict[str, Any],
    judge_ai: str,
    summary: str,
) -> Dict[str, Any]:
    """
    保存裁判总结到记忆库。

    同一场讨论只保留最新一份裁判总结（先清旧后写新），
    避免重复点击「裁判总结」在记忆库里堆积重复条目。
    返回 { memory_id, sections }，memory_id 会随 SSE 下发供前端跳转。
    """
    sections = parse_judge_sections(summary)
    discussion_id = discussion.get("id")

    try:
        await asyncio.to_thread(
            memory_repo.delete_by_source_sync, "discussion", discussion_id, "judge")
        memory_id = await asyncio.to_thread(
            memory_repo.insert_memory_sync,
            memory_type="judge",
            content=summary,
            topic=discussion.get("topic"),
            sections=sections,
            source_type="discussion",
            source_id=discussion_id,
            judge_model=judge_ai,
        )
    except Exception as e:  # 记忆写入失败不应打断总结流程
        logger.error("裁判总结写入记忆库失败: discussion=%s err=%s", discussion_id, e)
        return {"memory_id": None, "sections": sections}

    logger.info("裁判总结已入库: discussion=%s memory=%s sections=%s",
                discussion_id, memory_id, len(sections))
    return {"memory_id": memory_id, "sections": sections}


async def save_discussion_summary_memory(
    discussion: Dict[str, Any],
    ai_id: str,
    display_name: str,
    summary: str,
) -> Optional[int]:
    """保存讨论者 13 部分总结到记忆库（同一讨论+同一 AI 只留最新）。"""
    discussion_id = discussion.get("id")
    try:
        # 只覆盖「同一讨论 + 同一 AI」的旧总结，不影响其他讨论者的记录
        await asyncio.to_thread(
            _delete_by_ai, discussion_id, ai_id)
        return await asyncio.to_thread(
            memory_repo.insert_memory_sync,
            memory_type="discussion",
            content=summary,
            topic=discussion.get("topic"),
            source_type="discussion",
            source_id=discussion_id,
            ai_id=ai_id,
            display_name=display_name,
        )
    except Exception as e:
        logger.error("讨论总结写入记忆库失败: discussion=%s ai=%s err=%s",
                     discussion_id, ai_id, e)
        return None


def _delete_by_ai(discussion_id: int, ai_id: str) -> None:
    """删除同一讨论下同一 AI 的旧总结（直接走 repo 的事务连接）。"""
    from app.database.connection import get_transaction

    with get_transaction() as conn:
        conn.execute(
            """
            DELETE FROM memories
             WHERE source_type = 'discussion' AND source_id = ?
               AND memory_type = 'discussion' AND ai_id = ?
            """,
            (discussion_id, ai_id),
        )
