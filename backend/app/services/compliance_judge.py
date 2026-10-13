# ============================================================
# 文件：backend/app/services/compliance_judge.py
# 说明：合规裁判。每条发言后触发。
# 裁定 F1：DeepSeek 自判默认 passed（方案甲），不调用任何 AI。
# ============================================================

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import AI_MODELS, REASONING_MODELS, REASONING_MODEL_MAX_TOKENS
from app.config.business import DISCUSSION_JUDGES
from app.config.prompts import (
    DISC_COMPLIANCE_JUDGE_SYSTEM,
    DISC_COMPLIANCE_JUDGE_USER,
    DISC_JUDGE_MULTI_ROLE_GUARD,
    TEAM_AWARENESS,
    TEAM_ROLE_JUDGE_COMPLIANCE,
    build_prompt,
)
from app.services.ai_scheduler import call_ai

logger = logging.getLogger("app.compliance_judge")


# DeepSeek 是合规裁判的主模型；其自身发言跳过判定（裁定 F1）
_COMPLIANCE_JUDGE_AI = DISCUSSION_JUDGES["compliance"]["ai_id"]     # 'deepseek'
_COMPLIANCE_FALLBACK = DISCUSSION_JUDGES["compliance"].get("fallback", [])


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从文本中提取第一个 JSON 对象。"""
    if not text:
        return None
    # 尝试直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 提取 ```json ... ``` 或裸 JSON
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def check_compliance(
    *,
    discussion_id: int,
    topic: str,
    round_no: int,
    speaker_ai_id: str,
    speaker_display_name: str,
    content: str,
    recent_3_messages: List[Dict[str, Any]],
    judge_ai: Optional[str] = None,
    discussant_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    返回：{status, violation_type, reason, action}
    - speaker 与裁判同模型、且该模型不担任讨论席位 → 直接 passed（裁定 F1，省一次调用）
    - speaker 与裁判同模型、但该模型兼任讨论者 → 换一个不兼任的模型来判，避免自己监督自己
    - 其他情况，调用合规裁判模型进行判定
    - JSON 解析失败时，回退为 passed（保守）
    """
    judge_model = judge_ai or _COMPLIANCE_JUDGE_AI
    discussants = set(discussant_ids or [])
    is_self = speaker_ai_id == judge_model

    # 裁定 F1：仅在裁判模型不兼任讨论席位时成立；兼任时监督环节不能自我短路
    if is_self and judge_model not in discussants:
        return {
            "status": "passed",
            "violation_type": None,
            "reason": "Self-judgment bypassed (System Default)",
            "action": "none",
        }

    # 兼任讨论者：优先换一个既不同源、也不担任讨论席位的模型来判。
    # 官方直连优先于中转渠道——中转账户配额耗尽时（403）换过去只会白跑一次。
    if is_self and judge_model in discussants:
        candidates = [
            m for m in _COMPLIANCE_FALLBACK
            if m != judge_model and m not in discussants and m in AI_MODELS
        ]
        substitute = next(
            (m for m in candidates
             if AI_MODELS[m].channel == "official"),
            None,
        ) or (candidates[0] if candidates else None)
        if substitute:
            judge_model = substitute
            is_self = False

    # 组装历史
    recent_lines = "\n\n".join(
        f"[{m.get('display_name', m.get('ai_id'))}]: {m.get('content', '')[:300]}"
        for m in recent_3_messages
    ) or "（无）"

    user = build_prompt(
        DISC_COMPLIANCE_JUDGE_USER,
        topic=topic,
        ai_name=speaker_display_name,
        round=round_no,
        message_content=content,
        recent_3_messages=recent_lines,
    )
    # 团队意识：裁判也是协作系统里的一环——只判定、不代笔，结果交给下游环节
    system = DISC_COMPLIANCE_JUDGE_SYSTEM + build_prompt(
        TEAM_AWARENESS,
        role_line=TEAM_ROLE_JUDGE_COMPLIANCE,
        peers_line="协作席位：本场讨论者席位与「秩序裁判」席位；"
                   "你只做合规判定，判定结果会交给秩序裁判环节使用。",
    )
    # 裁判兼任讨论者（或换人失败只能自判）时注入身份锚定，防止自我偏袒
    if judge_model in discussants:
        system += "\n\n" + build_prompt(
            DISC_JUDGE_MULTI_ROLE_GUARD,
            role_label="实时监督裁判",
            other_roles="本场讨论者席位",
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    result = await call_ai(
        judge_model,
        messages,
        task_type="judge",
        task_id=discussion_id,
        fallback=_COMPLIANCE_FALLBACK,
        # 推理模型先消耗 reasoning token，512 会让它连 JSON 都吐不完
        max_tokens=(
            REASONING_MODEL_MAX_TOKENS if judge_model in REASONING_MODELS else 512
        ),
        temperature=0.2,
    )

    if result.status != "success":
        logger.warning(
            "合规判定调用失败，默认 passed: discussion=%s speaker=%s",
            discussion_id, speaker_ai_id,
        )
        return {
            "status": "passed",
            "violation_type": None,
            "reason": "合规裁判调用失败，系统默认通过",
            "action": "none",
        }

    parsed = _extract_json(result.content)
    if parsed is None:
        return {
            "status": "passed",
            "violation_type": None,
            "reason": "合规裁判返回无法解析，系统默认通过",
            "action": "none",
        }

    status = str(parsed.get("status", "passed")).lower()
    if status not in ("passed", "warning", "violation"):
        status = "passed"

    return {
        "status": status,
        "violation_type": parsed.get("violation_type"),
        "reason": parsed.get("reason"),
        "action": parsed.get("action", "none"),
    }
