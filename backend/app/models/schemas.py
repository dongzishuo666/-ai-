# ============================================================
# 文件：backend/app/models/schemas.py
# 说明：统一响应格式 Pydantic 模型。所有接口必须使用。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """统一成功响应：{code, data, message}"""
    code: int = 200
    data: Any = None
    message: str = "ok"


class ApiError(BaseModel):
    """统一错误响应：{code, error, message, detail}"""
    code: int
    error: str
    message: str
    detail: Optional[str] = None


# ============================================================
# 追加：功能三 · 文章生成相关模型
# ============================================================

from typing import Dict, Literal, Optional
from pydantic import BaseModel, Field


# 合法 genre 枚举（与 DDL CHECK 约束严格一致）
ArticleGenre = Literal[
    "tech_blog", "business", "academic", "essay", "news", "tutorial", "general"
]


class ArticleCreateRequest(BaseModel):
    """POST /api/articles/create 请求体（蓝图 3.1）"""
    topic: str = Field(..., min_length=1, max_length=200)
    genre: ArticleGenre = "general"
    target_word_count: int = Field(1500, ge=500, le=10000)
    style: Optional[str] = Field(None, max_length=100)
    additional_requirements: Optional[str] = Field(None, max_length=500)
    audience: Optional[str] = Field(None, max_length=100)
    # 自选三级写作模型：{"outliner": "deepseek", "writer": "deepseek", "reviewer": "deepseek"}
    # 留空则用系统默认角色分配。
    roles: Optional[Dict[str, str]] = None


class ArticleCreateData(BaseModel):
    """POST /api/articles/create 响应 data"""
    article_id: int
    topic: str
    status: str
    stream_url: str
    created_at: str


# ============================================================
# 追加：功能一 · 文档上传相关模型
# ============================================================

class DocUploadData(BaseModel):
    """POST /api/docs/upload 响应 data"""
    task_id: int
    filename: str
    doc_type: str
    file_size: int
    char_count: int
    status: str
    created_at: str


# ============================================================
# 追加：功能一 · process 请求/响应模型
# ============================================================

class DocProcessRequest(BaseModel):
    """POST /api/docs/process 请求体（蓝图 1.2）"""
    task_id: int
    user_need: str = Field(..., min_length=1, max_length=500)
    # 自选三棒模型：{"planner": "deepseek", "optimizer": "deepseek", "finalizer": "deepseek"}
    # 留空则用系统默认角色分配。用于降成本 / 指定特定模型。
    roles: Optional[Dict[str, str]] = None


class DocProcessData(BaseModel):
    """POST /api/docs/process 响应 data"""
    task_id: int
    status: str
    stream_url: str


# ============================================================
# 追加：功能二 · 讨论相关模型
# ============================================================

class DiscussionCreateRequest(BaseModel):
    """POST /api/discussions/create 请求体（蓝图 2.1 + 扩展V4 + V5 三人体制）"""
    topic: str = Field(..., min_length=1, max_length=500)
    # 轮次上限放宽到 10：开启「直到共识」后允许超过 3 轮继续讨论
    rounds: int = Field(3, ge=1, le=10)
    mode: Literal["free", "structured"] = "free"
    # 上限同步放宽到 1800s：10 轮长讨论无法在 600s 内跑完
    max_duration_seconds: int = Field(600, ge=60, le=1800)
    # ---- 扩展V4：风格模式 / 共识模式 / 认知记忆 ----
    style_mode: Literal["free", "planning", "affective", "roleplay"] = "free"
    until_consensus: bool = False
    memory_enabled: bool = False
    # ---- 扩展V5：自选阵容（3 位参赛 + 3 位裁判） ----
    # 留空则由后端按默认阵容（或智能组队结果）填充
    participants: Optional[List[str]] = Field(default=None, max_length=3)
    judges: Optional[Dict[str, str]] = None
    consensus_strictness: Literal["loose", "medium", "strict"] = "medium"


class ParticipantItem(BaseModel):
    """响应中的讨论者项"""
    ai_id: str
    display_name: str
    channel: str
    role: str


class JudgeItem(BaseModel):
    """响应中的裁判项"""
    judge_id: str
    display_name: str
    model: str
    duty: str
    # 模型展示名（弹劾换人后会变，前端据此显示当前是谁在判）
    model_display: str = ""


class DiscussionCreateData(BaseModel):
    """POST /api/discussions/create 响应 data"""
    discussion_id: int
    topic: str
    status: str
    total_rounds: int
    mode: str
    participants: List[ParticipantItem]
    judges: List[JudgeItem]
    stream_url: str
    created_at: str


# ============================================================
# 追加：功能二 · 暂停/恢复/终止 响应模型
# ============================================================

class DiscussionPauseData(BaseModel):
    discussion_id: int
    status: str
    paused_at: str
    current_round: int
    messages_so_far: int


class DiscussionResumeData(BaseModel):
    discussion_id: int
    status: str
    resumed_at: str
    resume_round: int
    stream_url: str


class DiscussionTerminateData(BaseModel):
    discussion_id: int
    status: str
    end_reason: str
    terminated_at: str
    rounds_completed: int
