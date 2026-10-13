"""全平台 8 个可调用 AI 的注册表与渠道配置。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass(frozen=True)
class AIProfile:
    id: str
    name: str
    provider: str          # openai / anthropic / gemini
    channel: str           # relay（中转） / official（官方）
    model: str
    color: str
    env_key: str
    base_url_env: str
    default_base_url: str
    tagline: str = ""


# 固定 8 个 AI：4 中渠 + 4 官方
REGISTRY: dict[str, AIProfile] = {
    # ===== 中转 API 渠道 =====
    "gemini": AIProfile(
        id="gemini", name="Gemini", provider="gemini", channel="relay",
        model="gemini-2.0-flash", color="#4285F4",
        env_key="GEMINI_API_KEY", base_url_env="GEMINI_BASE_URL",
        default_base_url="https://generativelanguage.googleapis.com",
        tagline="多模态推理 · 长上下文",
    ),
    "openai": AIProfile(
        id="openai", name="OpenAI", provider="openai", channel="relay",
        model="gpt-4o-mini", color="#10A37F",
        env_key="OPENAI_API_KEY", base_url_env="OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        tagline="通用能力标杆",
    ),
    "claude": AIProfile(
        id="claude", name="Claude", provider="anthropic", channel="relay",
        model="claude-3-5-sonnet-latest", color="#D97757",
        env_key="ANTHROPIC_API_KEY", base_url_env="ANTHROPIC_BASE_URL",
        default_base_url="https://api.anthropic.com",
        tagline="长文写作 · 结构化思考",
    ),
    "grok": AIProfile(
        id="grok", name="Grok", provider="openai", channel="relay",
        model="grok-2-latest", color="#1D1D1F",
        env_key="GROK_API_KEY", base_url_env="GROK_BASE_URL",
        default_base_url="https://api.x.ai/v1",
        tagline="实时性 · 观点锐利",
    ),
    # ===== 官方 API 渠道 =====
    "deepseek": AIProfile(
        id="deepseek", name="DeepSeek", provider="openai", channel="official",
        model="deepseek-chat", color="#4D6BFE",
        env_key="DEEPSEEK_API_KEY", base_url_env="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com/v1",
        tagline="中文推理 · 高性价比",
    ),
    "zhipu": AIProfile(
        id="zhipu", name="智谱 GLM", provider="openai", channel="official",
        model="glm-4-flash", color="#2D5AF0",
        env_key="ZHIPU_API_KEY", base_url_env="ZHIPU_BASE_URL",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        tagline="中文理解 · 工具调用",
    ),
    "ernie": AIProfile(
        id="ernie", name="文心一言", provider="openai", channel="official",
        model="ernie-4.0-turbo-8k", color="#2932E1",
        env_key="QIANFAN_API_KEY", base_url_env="QIANFAN_BASE_URL",
        default_base_url="https://qianfan.baidubce.com/v2",
        tagline="中文知识 · 内容安全",
    ),
    "kimi": AIProfile(
        id="kimi", name="Kimi", provider="openai", channel="official",
        model="moonshot-v1-8k", color="#111827",
        env_key="MOONSHOT_API_KEY", base_url_env="MOONSHOT_BASE_URL",
        default_base_url="https://api.moonshot.cn/v1",
        tagline="超长文本 · 资料检索",
    ),
}

ALL_IDS: list[str] = list(REGISTRY.keys())


def get_profile(ai_id: str) -> AIProfile:
    return REGISTRY[ai_id]


def resolve_endpoint(profile: AIProfile) -> tuple[str, str]:
    """返回该 AI 实际使用的 (base_url, api_key)。"""
    if profile.channel == "relay" and settings.relay_base_url and settings.relay_api_key:
        return settings.relay_base_url, settings.relay_api_key
    api_key = settings.env(profile.env_key)
    base_url = settings.env(profile.base_url_env, profile.default_base_url)
    return base_url.rstrip("/"), api_key


def is_ready(profile: AIProfile) -> bool:
    """该模型是否具备真实调用条件。"""
    if settings.force_mock:
        return False
    _, api_key = resolve_endpoint(profile)
    return bool(api_key)


def public_ai_list() -> list[dict]:
    """给前端使用的 AI 元数据（不含任何密钥）。"""
    return [
        {
            "id": p.id,
            "name": p.name,
            "channel": p.channel,
            "channelLabel": "中转 API" if p.channel == "relay" else "官方 API",
            "model": p.model,
            "color": p.color,
            "tagline": p.tagline,
            "ready": is_ready(p),
        }
        for p in REGISTRY.values()
    ]
