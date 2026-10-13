# ============================================================
# 文件：backend/app/utils/text_utils.py
# 说明：文本处理工具。纯函数，无副作用。
# ============================================================

from __future__ import annotations


# 智能截断参数（与蓝图 MEMORY_LIMITS.max_raw_text_chars=8000 严格对齐）
_HEAD_LEN = 4000
_MID_LEN = 2000
_TAIL_LEN = 2000
_TOTAL_LEN = _HEAD_LEN + _MID_LEN + _TAIL_LEN   # = 8000


def truncate_text(text: str, max_chars: int = _TOTAL_LEN) -> str:
    """
    智能截断：保留头部 + 中部 + 尾部，严格 ≤ max_chars。
    - 头部：前 4000 字符（开头信息密度最高）
    - 中部：从原文中心抽取 2000 字符（保留核心论述）
    - 尾部：最后 2000 字符（结论与总结）

    说明：默认 max_chars=8000 时，严格按 4000/2000/2000 分配；
    若 max_chars 被覆盖，按 50% / 25% / 25% 比例分配。
    """
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text

    if max_chars == _TOTAL_LEN:
        head_len, mid_len, tail_len = _HEAD_LEN, _MID_LEN, _TAIL_LEN
    else:
        head_len = max_chars // 2
        mid_len = max_chars // 4
        tail_len = max_chars - head_len - mid_len

    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    mid_center = len(text) // 2
    mid_start = max(head_len, mid_center - mid_len // 2)
    mid = text[mid_start:mid_start + mid_len]

    # 拼接后长度天然 ≤ max_chars（head+mid+tail 之和）
    result = head + mid + tail
    return result[:max_chars]


def count_chars(text: str) -> int:
    """字符数统计（去除所有空白）。"""
    if not text:
        return 0
    return len(text)
