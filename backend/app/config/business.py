# ============================================================
# 文件：backend/app/config/business.py
# 说明：业务配置唯一来源。角色分配、打分权重、禁言阈值、讨论参数、
#       内存限制全部集中于此；业务代码中禁止硬编码这些值。
# ============================================================

from typing import Any, Dict, List


# ============================================================
# 1. 功能一：文档成品生成 — 三角色分配
# ============================================================

DOC_PROCESS_ROLES: Dict[str, Dict[str, Any]] = {
    "planner": {
        "ai_id": "claude",
        # 末尾追加官方直连模型：中转渠道配额耗尽时（403 insufficient_quota）
        # 仍可由官方模型接手，避免整条流水线直接失败。
        "fallback": ["openai", "gemini", "deepseek", "zhipu"],
        "description": "第一棒：基于文档与需求直接产出成品初稿",
    },
    "optimizer": {
        "ai_id": "deepseek",
        "fallback": ["zhipu", "kimi"],
        "description": "第二棒：对照原文审校补强，输出更完整的成品",
    },
    "finalizer": {
        "ai_id": "openai",
        "fallback": ["claude", "gemini", "deepseek", "kimi"],
        "description": "第三棒：终审整合，输出可直接交付的成品",
    },
}


# ============================================================
# 2. 功能三：AI 三级文章生成 — 三角色分配
# ============================================================

ARTICLE_GEN_ROLES: Dict[str, Dict[str, Any]] = {
    "outliner": {
        "ai_id": "zhipu",
        "fallback": ["kimi", "wenxin"],
        "description": "第一棒：生成专业大纲",
    },
    "writer": {
        "ai_id": "deepseek",
        "fallback": ["zhipu", "kimi"],
        "description": "第二棒：基于大纲撰写完整初稿",
    },
    "reviewer": {
        "ai_id": "claude",
        # 末尾追加官方直连模型：中转渠道配额耗尽（403 insufficient_quota）时
        # 仍可由官方模型接手，否则第三棒会直接失败、整篇文章拿不到定稿。
        "fallback": ["openai", "gemini", "deepseek", "zhipu"],
        "description": "第三棒：审核修改、定稿输出",
    },
}


# ============================================================
# 3. 功能二：多 AI 讨论 — 裁判分配
# ============================================================

DISCUSSION_JUDGES: Dict[str, Dict[str, Any]] = {
    "compliance": {
        "ai_id": "deepseek",
        "fallback": ["openai", "zhipu"],
        "description": "实时监督裁判：每条发言后即时判定合规性",
        "trigger": "after_each_message",
    },
    "order": {
        "ai_id": "openai",
        "fallback": ["claude", "deepseek"],
        "description": "秩序裁判：发言顺序管控、禁言执行、计分更新",
        "trigger": "after_each_round",
    },
    "evaluation": {
        "ai_id": "claude",
        "fallback": ["openai", "deepseek"],
        "description": "最终评价裁判：终局评估、排名、总结、行动方案",
        "trigger": "at_discussion_end",
    },
}


# ============================================================
# 4. 功能二：多 AI 讨论 — 参与者
# ============================================================

# 候选池：智能组队从中挑选（保持 8 个，供 AI 推荐/用户自选）
DISCUSSION_PARTICIPANTS: List[str] = [
    "gemini",
    "openai",
    "claude",
    "grok",
    "deepseek",
    "zhipu",
    "wenxin",
    "kimi",
    "spark",
    "hunyuan",
]

# 默认上场的 3 位讨论者（未指定时使用）
DISCUSSION_DEFAULT_PARTICIPANTS: List[str] = [
    "gemini",
    "claude",
    "deepseek",
]

# 每场讨论固定人数
DISCUSSION_PARTICIPANT_COUNT: int = 3

# 讨论发言顺序策略
DISCUSSION_ORDER_STRATEGY: str = "round_robin"


# ============================================================
# 4b. 共识模式配置（直到共识）
# ============================================================

CONSENSUS_CONFIG: Dict[str, Any] = {
    # 从第几轮开始做共识判定（含该轮）——需求：三轮以后（包含三轮）
    "min_check_round": 3,
    # 发散期轮数：前 N 轮放开讨论（鼓励新角度、新论据），
    # 第 N 轮结束后由「评价裁判」产出一份局中总结，之后进入收敛期，
    # 参与者围绕该总结收敛观点；共识判定也从第 N+1 轮才开始（发散期判定没有意义）。
    "diverge_rounds": 3,
    # 共识模式下允许的最大轮次（可超过基础轮次，突破 3 轮限制）
    "max_rounds": 10,
    # 达到该轮次仍未共识则强制结束（兜底，防止无限循环）
    "hard_stop_round": 10,
    # 严格程度 → 判定口径说明（注入裁判 Prompt）
    "strictness_desc": {
        "loose": "宽松：只要核心方向上没有明显对立，即可判定为达成共识",
        "medium": "中等：主要分歧已被回应或部分收敛，可判定为达成共识",
        "strict": "严格：必须在核心结论上形成明确一致意见，才可判定为达成共识",
    },
    # 严格程度 → 实际判定规则（注入裁判 Prompt）
    # 之前只有上面的「说明」而没有「规则」，裁判 Prompt 里又写死了最严口径
    # （必须"明确、稳定"的共同立场），导致 loose/medium 形同虚设。
    "criteria": {
        "loose": "只要参与者在核心方向上没有明显对立，次要分歧与不同表述均可保留，即可判定为达成共识。",
        "medium": "主要分歧已被正面回应（接受、反驳并给出理由、或收敛到某条件），或各方立场已明显靠近，即可判定为达成共识。",
        "strict": "必须在核心结论上形成明确一致的立场（次要分歧可保留），才可判定为达成共识。",
    },
}


# ============================================================
# 4c. 智能组队配置
# ============================================================

TEAMUP_CONFIG: Dict[str, Any] = {
    "participant_count": 3,          # 选出的参赛 AI 数量
    "judge_slots": ["compliance", "order", "evaluation"],   # 需选出的 3 个裁判位
    "council_size": 3,               # 多AI协商时参与投票的 AI 数量
}


# ============================================================
# 5. 打分配置
# ============================================================

SCORING_WEIGHTS: Dict[str, float] = {
    "quality": 0.35,       # 发言质量
    "relevance": 0.25,     # 主题相关性
    "contribution": 0.25,  # 贡献度
    "compliance": 0.15,    # 合规性
}

SCORING_THRESHOLD: Dict[str, Any] = {
    "ban_score_threshold": 60.0,       # 低于此分数触发禁言判定
    "ban_consecutive_rounds": 2,       # 连续末位轮数
    "violation_ban_rounds": 1,         # 违规禁言轮数
    "low_score_ban_rounds": 1,         # 低分禁言轮数
    # 最低活跃讨论者数量。3 人制下需至少保留 2 人，否则禁言会直接终止讨论
    "min_active_participants": 2,
}


# ============================================================
# 6. 讨论参数
# ============================================================

DISCUSSION_CONFIG: Dict[str, Any] = {
    "max_rounds": 10,                  # 普通模式上限（共识模式下限由 CONSENSUS_CONFIG 控制）
    "default_rounds": 3,
    # 上限放宽到 30 分钟：10 轮讨论（含评分、共识判定、终局评估）普遍超过原先的 600s
    "max_duration_seconds": 1800,
    "default_duration_seconds": 300,
    # 僵尸任务回收阈值：非终结态且超过该时长毫无进展 → 自动置为终止态。
    # 缺了这个兜底，一次崩溃/断连遗留的 running 记录会永久阻塞新建讨论。
    "stale_task_seconds": 1800,
    "max_message_length": 2000,        # 单条发言最大字符数
    "min_message_length": 20,          # 单条发言最小字符数
    "context_window_messages": 8,      # 传给每个 AI 的上下文窗口
    "round_summary_enabled": True,     # 每轮结束是否生成轮次总结
}


# ============================================================
# 7. 轻量化约束（内存与存储）
# ============================================================

MEMORY_LIMITS: Dict[str, int] = {
    "max_upload_file_mb": 10,
    "max_raw_text_chars": 8000,        # 文档原文最大存储字符数
    "max_response_preview_chars": 500, # AI 响应预览最大字符数
    "max_context_tokens": 6000,        # 传给 AI 的最大上下文 token 数
    "log_retention_days": 7,           # 日志保留天数
}


# ============================================================
# 追加：文体标签映射（功能三）
# 说明：全项目唯一真相源。prompts.py 从此处导入。
# ============================================================

GENRE_LABELS: Dict[str, str] = {
    "tech_blog": "技术博客",
    "business": "商业文案",
    "academic": "学术论文",
    "essay": "散文/随笔",
    "news": "新闻稿",
    "tutorial": "教程/指南",
    "general": "通用文章",
}
