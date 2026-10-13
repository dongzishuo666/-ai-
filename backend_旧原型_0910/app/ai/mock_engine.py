"""本地模拟引擎。

用途：未配置任何 API Key 时，三大功能依然可以完整跑通、流式打字输出，
方便快速体验与前端联调（MVP 原型的关键）。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator


async def stream_text(text: str, chunk: int = 3, delay: float = 0.015) -> AsyncIterator[str]:
    """按小块逐字输出，模拟真实模型的流式打字效果。"""
    for i in range(0, len(text), chunk):
        yield text[i : i + chunk]
        await asyncio.sleep(delay)


def _short(text: str, limit: int = 60) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


# ============================ 模块一：Prompt 生成 ============================

def mock_prompt_stage(stage: str, need: str, doc_summary: str, prev: str = "") -> str:
    topic = _short(need, 40) or "通用任务"
    if stage == "parse":
        return (
            "1. 用户核心目标：基于上传文档产出可直接复用的高质量 Prompt，用于「"
            f"{topic}」场景。\n"
            f"2. 文档核心内容摘要：{_short(doc_summary, 90) or '（未上传文档，仅依据文字需求）'}\n"
            "3. 输出格式要求：结构化、要素完整、可直接粘贴进大模型使用。\n"
            "4. 关键约束条件：明确角色定位、限定输出边界、避免歧义、控制篇幅。"
        )
    if stage == "generate":
        return (
            "【角色定位】你是资深领域专家，擅长把零散需求转化为可执行方案。\n"
            f"【任务目标】围绕「{topic}」输出一份结构完整、可直接落地的成果。\n"
            "【输入信息】\n"
            f"- 文档要点：{_short(doc_summary, 80)}\n"
            f"- 用户需求：{_short(need, 80)}\n"
            "【输出规范】\n1. 先给出结论，再分层展开；\n2. 使用 Markdown 标题与要点列表；\n"
            "3. 关键数据用表格呈现；\n4. 全文不超过 800 字，避免空话。\n"
            "【约束条件】不得编造未提供的事实；信息不足时明确说明缺失项。"
        )
    if stage == "optimize":
        base = prev or "（初版 Prompt）"
        return (
            base
            + "\n\n【优化增强】\n"
            "- 补充「输出前自检」环节：核对是否覆盖全部约束、是否存在歧义。\n"
            "- 明确角色边界与拒答规则，避免越界输出。\n"
            "- 增加示例片段，帮助模型对齐格式预期。\n"
            "- 删除重复修饰词，指令改为动宾短句，提升执行效率。"
        )
    # finalize
    base = prev or "（优化版 Prompt）"
    return (
        f"# 最终可用 Prompt（场景：{topic}）\n\n"
        + base
        + "\n\n【执行前自检清单】\n"
        "1. 角色、任务、输出格式三项是否已明确？\n"
        "2. 是否包含防编造与信息不足时的处理约定？\n"
        "3. 是否满足用户原始需求中的全部约束？\n"
        "→ 全部通过后，直接输出最终结果。"
    )


# ============================ 模块二：多 AI 讨论 ============================

_SPEECH_ANGLES = {
    "gemini": "从跨模态与全局结构的角度",
    "openai": "从通用最佳实践与风险控制的角度",
    "claude": "从长程逻辑与论证严谨性的角度",
    "grok": "从反常识与质疑默认假设的角度",
    "deepseek": "从中文语境与推理链路的角度",
    "zhipu": "从工程落地与可执行性的角度",
    "ernie": "从合规、安全与用户价值的的角度",
    "kimi": "从资料检索与案例举证的角度",
}

_MODE_TONE = {
    "planning": "聚焦可执行建议",
    "affective": "以感受与想象展开",
    "roleplay": "完全代入所扮演角色",
    "custom": "遵循自定义规则",
    "default": "理性而直接",
}


def mock_speech(ai_id: str, ai_name: str, topic: str, mode: str, round_no: int) -> str:
    angle = _SPEECH_ANGLES.get(ai_id, "从一个独特的角度")
    tone = _MODE_TONE.get(mode, _MODE_TONE["default"])
    t = _short(topic, 30) or "当前议题"
    if round_no <= 1:
        return (
            f"我是 {ai_name}。关于「{t}」，我{tone}，{angle}提出三点：\n"
            f"1. 先明确目标与边界——这个议题真正要解决的问题是「{t}」的核心矛盾；\n"
            "2. 给出可验证的第一步动作，而不是停留在概念层面；\n"
            "3. 预设一个失败场景与应对方式，降低试错成本。"
        )
    if round_no == 2:
        return (
            f"我是 {ai_name}。回应前面的讨论，我保留部分意见：\n"
            f"- 认同其中有价值的部分，但对「{t}」的可行性我持谨慎态度；\n"
            "- 补充一个被忽略的维度：执行主体的能力与资源约束；\n"
            "- 建议用一个小规模试点验证，再决定是否放大投入。"
        )
    return (
        f"我是 {ai_name}。综合目前所有发言，我倾向于收敛方案：\n"
        f"- 对「{t}」给出一个分阶段路线：验证 → 迭代 → 推广；\n"
        "- 明确衡量指标与退出条件，避免沉没成本；\n"
        "- 我愿意在共识基础上让步，只要核心目标不被牺牲。"
    )


def mock_judge_anomaly(content: str) -> tuple[bool, str]:
    """模拟监督员：命中抬杠/空话特征则判异常。"""
    bad_markers = ["无所谓", "随便", "不知道", "废话"]
    for m in bad_markers:
        if m in content:
            return True, "发言空泛或态度敷衍，未回应当前议题"
    return False, "发言切题，逻辑完整"


def mock_scores(ai_id: str, round_no: int) -> float:
    """基于模型 id 做稳定伪随机，保证同一 AI 分数连贯。"""
    seed = sum(ord(c) for c in ai_id) + round_no * 7
    return round(6.0 + (seed % 40) / 10.0, 1)  # 6.0 ~ 9.9


def mock_consensus(round_no: int) -> bool:
    return round_no >= 4


def mock_action_plan(topic: str) -> str:
    return (
        "# 行动方案\n\n"
        f"**议题**：{topic}\n\n"
        "| 时间 | 项目 | 负责人 | 预算 | 交付物 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 第 1 周 | 目标澄清与范围界定 | 发起方 | 0 | 需求说明 |\n"
        "| 第 2-3 周 | 小规模试点验证 | 执行组 | 低 | 试点报告 |\n"
        "| 第 4 周 | 复盘与迭代 | 全体 | 低 | 迭代方案 |\n"
        "| 第 5-8 周 | 放大推广 | 执行组 | 中 | 落地成果 |\n\n"
        "## 关键里程碑\n"
        "1. 试点验证通过率 ≥ 80%\n2. 单位成本下降 ≥ 15%\n3. 无重大合规问题\n"
    )


def mock_report(topic: str, participants: list[str]) -> str:
    names = "、".join(participants) if participants else "全体参与者"
    return (
        "# 讨论复盘评估\n\n"
        f"**主题**：{topic}\n\n"
        "## 1. 讨论效率评分\n7 / 10 —— 观点覆盖全面，但对执行路径的收敛稍慢。\n\n"
        "## 2. 各 AI 贡献度排名\n"
        f"参与模型：{names}\n"
        "1. 首位 —— 提出结构化框架，奠定讨论基调；\n"
        "2. 次位 —— 补充关键约束，避免方案冒进；\n"
        "3. 其余 —— 提供差异化视角，丰富备选方案。\n\n"
        "## 3. 未解决的问题\n"
        "- 预算上限未明确；\n- 责任边界仍需确认；\n- 长期收益缺乏数据支撑。\n\n"
        "## 4. 后续建议\n"
        "优先完成小范围验证，收集真实数据后再进入推广阶段。\n"
    )


# ============================ 模块三：文章生成 ============================

def mock_outline(topic: str, genre: str, style: str, length: str) -> str:
    t = _short(topic, 30) or "指定主题"
    return (
        f"# {t}（{genre or '通用'} · {style or '中性'} · {length or '中篇'}）\n\n"
        "## 一、引言：为什么这件事值得关注\n"
        "- 背景与现状\n- 提出核心问题\n\n"
        "## 二、核心分析：问题的结构\n"
        "- 关键变量拆解\n- 主要矛盾与次要矛盾\n\n"
        "## 三、路径探索：可行的解法\n"
        "- 短期可行动作\n- 中期结构性调整\n\n"
        "## 四、风险与边界\n"
        "- 潜在风险清单\n- 不适用场景说明\n\n"
        "## 五、结语：从认知到行动\n"
        "- 核心结论回收\n- 给出第一步建议"
    )


def mock_draft(topic: str, outline: str) -> str:
    t = _short(topic, 30) or "该主题"
    return (
        f"# {t}\n\n"
        f"围绕「{t}」，本文按照既定大纲展开论述。\n\n"
        "## 引言\n"
        "任何值得认真对待的问题，往往不是缺少观点，而是缺少一个清晰的结构。"
        f"本文试图为「{t}」建立一个可讨论、可验证的分析框架。\n\n"
        "## 核心分析\n"
        "问题的难点并不在于信息不足，而在于变量太多、彼此纠缠。"
        "因此我们先做拆解：把可控制的因素与不可控的因素分开，"
        "再把短期目标与长期目标分开，避免在混乱中下结论。\n\n"
        "## 路径探索\n"
        "在短期，应先完成一次低成本的验证，用真实反馈替代估计；"
        "在中期，则需要围绕验证结果调整资源分配，把有效做法固化下来。\n\n"
        "## 风险与边界\n"
        "必须承认，任何方案都有适用边界。数据不足、执行资源有限、外部环境变化，"
        "都会让结论打折扣，因此需要预留退出与回调机制。\n\n"
        "## 结语\n"
        f"回到「{t}」本身，最重要的不是一次性找到完美答案，"
        "而是建立一个可以持续修正的过程。"
    )


def mock_final(topic: str, draft: str) -> str:
    t = _short(topic, 30) or "该主题"
    return (
        f"# {t}\n\n"
        "## 引言\n"
        "值得认真对待的问题，往往不是缺少观点，而是缺少清晰的结构。"
        f"本文为「{t}」建立一个可讨论、可验证的分析框架。\n\n"
        "## 核心分析\n"
        "真正的难点不在于信息不足，而在于变量过多、彼此纠缠。"
        "先把可控因素与不可控因素分开，再把短期目标与长期目标分开，"
        "才能避免在混乱中仓促下结论。\n\n"
        "## 路径探索\n"
        "短期之内，应以一次低成本验证替代主观估计，用真实反馈校准判断；"
        "中期则围绕验证结果重新分配资源，把被证明有效的做法固化为机制。\n\n"
        "## 风险与边界\n"
        "任何方案都有适用边界。数据不足、资源有限或环境突变都会削弱结论，"
        "因此需要预留退出机制与回调路径，而不是一味加注。\n\n"
        "## 结语\n"
        f"回到「{t}」本身，重要的不是一次性找到完美答案，"
        "而是建立一个能够持续修正、逐步逼近正确的过程。\n\n"
        "—— 全文完 ——"
    )


def mock_revision(text: str, instruction: str) -> str:
    return (
        text.rstrip()
        + "\n\n## 修订说明（依据用户意见）\n"
        f"- 已按意见「{_short(instruction, 60)}」完成调整；\n"
        "- 优化了段落过渡与语气一致性；\n"
        "- 补充了结论部分的可执行建议。\n\n"
        "（以上为修订后完整文章，可直接使用。）"
    )
