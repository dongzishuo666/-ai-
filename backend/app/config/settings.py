# ============================================================
# 文件：backend/app/config/settings.py
# 说明：全局配置唯一来源。所有配置从 .env 读取，并提供默认值。
# 约束：任何位置不得硬编码真实密钥；密钥一律通过 .env 注入。
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv


# ============================================================
# 0. 环境加载与辅助函数
# ============================================================

# settings.py 位于 backend/app/config/settings.py → 上溯三级为 backend/
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

# 加载 .env（若不存在则使用系统环境变量）
load_dotenv(BASE_DIR / ".env", override=False)


def _get_str(key: str, default: str = "") -> str:
    """读取字符串，缺失时返回默认值（自动 strip）。"""
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip()
    return value if value != "" else default


def _get_int(key: str, default: int) -> int:
    """读取整数，解析失败时回退默认值（防止启动崩溃）。"""
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        return default


def _get_float(key: str, default: float) -> float:
    """读取浮点数，解析失败时回退默认值。"""
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    """读取布尔值，支持 true/false/1/0/yes/no。"""
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("true", "1", "yes", "y", "on")


def _resolve_path(raw: str) -> Path:
    """相对路径基于 BASE_DIR 解析，绝对路径原样返回。"""
    path = Path(raw)
    if path.is_absolute():
        return path
    return (BASE_DIR / path).resolve()


# ============================================================
# 1. 运行时参数（前置：AI_MODELS 需引用 AI_CALL_TIMEOUT）
# ============================================================

# AI 调用
AI_MAX_CONCURRENT: int = _get_int("AI_MAX_CONCURRENT", 3)
AI_CALL_TIMEOUT: int = _get_int("AI_CALL_TIMEOUT", 45)
AI_RETRY_DELAY_BASE: float = _get_float("AI_RETRY_DELAY_BASE", 1.0)
AI_RETRY_DELAY_MULTIPLIER: float = _get_float("AI_RETRY_DELAY_MULTIPLIER", 2.0)
AI_FALLBACK_ENABLED: bool = _get_bool("AI_FALLBACK_ENABLED", True)
AI_SKIP_ON_FAILURE: bool = _get_bool("AI_SKIP_ON_FAILURE", True)

# 单模型并发上限：部分供应商账户并发极低（实测 Kimi 官方账户
# max organization concurrency = 1），多席位并行发言会直接撞 429。
# 这里按模型再串行一层，比全局并发更贴近供应商的真实限制。
AI_MODEL_MAX_CONCURRENT: Dict[str, int] = {
    "kimi": _get_int("KIMI_MAX_CONCURRENT", 1),
}

# 撞限流（429）后的等待秒数：这类错误是瞬时的，退避后重试比直接放弃划算
AI_RATE_LIMIT_WAIT: float = _get_float("AI_RATE_LIMIT_WAIT", 3.0)

# 推理型模型：输出前会先在 reasoning 上消耗 token，同一 max_tokens 下
# 正文会比普通模型短很多（实测 kimi-k2.6 给 1024 时正文只剩十几个字）。
# 这类模型需要给足额度，否则发言会残缺到被判违规。
# 推理模型：会先输出 reasoning_content，再输出正文
# （kimi-k2 / 讯飞 X 系列 spark-x2.5 / 腾讯混元 hy3）
REASONING_MODELS: set = {"kimi", "spark", "hunyuan"}

# 推理模型在讨论发言时的 token 额度（普通模型沿用调用处给定的 1024）
REASONING_MODEL_MAX_TOKENS: int = _get_int("REASONING_MODEL_MAX_TOKENS", 4096)

# 推理模型的 token 下限：调用处给的额度低于此值时自动抬到该值。
# 覆盖全部调用点（对话 128、组队 512、共识检测 512 等），
# 否则这类模型把 token 耗在 reasoning 上，正文会直接返回空串。
# 取 2048 是实测值：讯飞 spark-x2.5-4b 的思考常占 400~1000 token，
# 给 1024 时正文仍被吃光，2048 起稳定吐出正文。
REASONING_MODEL_MIN_TOKENS: int = _get_int("REASONING_MODEL_MIN_TOKENS", 2048)

# SSE
SSE_HEARTBEAT_INTERVAL: int = _get_int("SSE_HEARTBEAT_INTERVAL", 15)

# 讨论
PAUSE_TIMEOUT_SECONDS: int = _get_int("PAUSE_TIMEOUT_SECONDS", 600)

# 日志
LOG_RETENTION_DAYS: int = _get_int("LOG_RETENTION_DAYS", 7)
LOG_LEVEL: str = _get_str("LOG_LEVEL", "info").lower()


# ============================================================
# 2. 渠道连接配置（仅连接信息，不含模型参数与密钥硬编码）
# ============================================================

@dataclass(frozen=True)
class RelayConnection:
    """中转渠道连接信息（OpenAI 兼容格式）。"""
    base_url: str
    api_key: str


@dataclass(frozen=True)
class OfficialConnection:
    """官方渠道连接信息。secret_key 仅文心一言使用。"""
    base_url: str
    api_key: str
    secret_key: str = ""


RELAY = RelayConnection(
    base_url=_get_str("RELAY_API_BASE_URL", "https://your-relay-provider.com/v1"),
    api_key=_get_str("RELAY_API_KEY", ""),
)

DEEPSEEK = OfficialConnection(
    base_url=_get_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    api_key=_get_str("DEEPSEEK_API_KEY", ""),
)

ZHIPU = OfficialConnection(
    base_url=_get_str("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    api_key=_get_str("ZHIPU_API_KEY", ""),
)

WENXIN = OfficialConnection(
    base_url=_get_str(
        "WENXIN_BASE_URL",
        "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat",
    ),
    api_key=_get_str("WENXIN_API_KEY", ""),
    secret_key=_get_str("WENXIN_SECRET_KEY", ""),
)

KIMI = OfficialConnection(
    base_url=_get_str("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
    api_key=_get_str("KIMI_API_KEY", ""),
)

# 腾讯混元（tokenhub 网关，OpenAI 兼容）。服务 ID 固定为 hy3。
HUNYUAN = OfficialConnection(
    base_url=_get_str(
        "HUNYUAN_BASE_URL", "https://tokenhub-intl.tencentmaas.com/v1"),
    api_key=_get_str("HUNYUAN_API_KEY", ""),
)

# 讯飞星辰 MaaS（OpenAI 兼容）。模型名区分大小写，须用小写 spark-x2.5-4b。
SPARK = OfficialConnection(
    base_url=_get_str(
        "SPARK_BASE_URL", "https://maas-api.cn-huabei-1.xf-yun.com/v1"),
    api_key=_get_str("SPARK_API_KEY", ""),
)


# ============================================================
# 3. AI 模型元信息（模型名从 .env 读取；timeout 引用全局 AI_CALL_TIMEOUT）
# ============================================================

@dataclass(frozen=True)
class ModelConfig:
    ai_id: str
    display_name: str
    channel: str          # relay / official
    model_name: str
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout_seconds: int = AI_CALL_TIMEOUT   # ← 修正1：引用全局变量
    retry_count: int = 2
    priority: int = 1


AI_MODELS: Dict[str, ModelConfig] = {
    # ---------- 中转渠道 ----------
    "gemini": ModelConfig(
        ai_id="gemini",
        display_name="Gemini",
        channel="relay",
        model_name=_get_str("RELAY_MODEL_GEMINI", "gemini-2.0-flash"),
        temperature=0.7,
        priority=2,
    ),
    "openai": ModelConfig(
        ai_id="openai",
        display_name="OpenAI GPT",
        channel="relay",
        model_name=_get_str("RELAY_MODEL_OPENAI", "gpt-4o"),
        temperature=0.7,
        priority=1,
    ),
    "claude": ModelConfig(
        ai_id="claude",
        display_name="Claude",
        channel="relay",
        model_name=_get_str("RELAY_MODEL_CLAUDE", "claude-3.5-sonnet"),
        temperature=0.7,
        priority=1,
    ),
    "grok": ModelConfig(
        ai_id="grok",
        display_name="Grok",
        channel="relay",
        model_name=_get_str("RELAY_MODEL_GROK", "grok-2"),
        temperature=0.8,
        priority=3,
    ),
    # ---------- 官方渠道 ----------
    "deepseek": ModelConfig(
        ai_id="deepseek",
        display_name="DeepSeek",
        channel="official",
        model_name=_get_str("DEEPSEEK_MODEL", "deepseek-chat"),
        priority=1,
    ),
    "zhipu": ModelConfig(
        ai_id="zhipu",
        display_name="智谱清言",
        channel="official",
        model_name=_get_str("ZHIPU_MODEL", "glm-4-flash"),
        priority=2,
    ),
    "wenxin": ModelConfig(
        ai_id="wenxin",
        display_name="文心一言",
        channel="official",
        model_name=_get_str("WENXIN_MODEL", "ernie-4.0-8k"),
        priority=2,
    ),
    "kimi": ModelConfig(
        ai_id="kimi",
        display_name="Kimi",
        channel="official",
        model_name=_get_str("KIMI_MODEL", "moonshot-v1-8k"),
        priority=2,
        # kimi-k2.6 是推理模型，单次响应常在 25-30 秒，45 秒默认超时容易误杀
        timeout_seconds=_get_int("KIMI_TIMEOUT", 90),
    ),
    "hunyuan": ModelConfig(
        ai_id="hunyuan",
        display_name="腾讯混元",
        channel="official",
        model_name=_get_str("HUNYUAN_MODEL", "hy3"),
        priority=2,
        # hy3 是推理模型，思考链会占用额度（实测 2~5 秒）
        timeout_seconds=_get_int("HUNYUAN_TIMEOUT", 90),
    ),
    "spark": ModelConfig(
        ai_id="spark",
        display_name="讯飞星火",
        channel="official",
        model_name=_get_str("SPARK_MODEL", "spark-x2.5-4b"),
        priority=2,
        # X 系列思考链较长（实测 5~10 秒），默认超时容易误杀
        timeout_seconds=_get_int("SPARK_TIMEOUT", 90),
    ),
}

# 渠道 → 连接信息映射（供 ai_scheduler 路由使用）
CHANNEL_CONNECTIONS = {
    "relay": RELAY,
    "deepseek": DEEPSEEK,
    "zhipu": ZHIPU,
    "wenxin": WENXIN,
    "kimi": KIMI,
    "spark": SPARK,
    "hunyuan": HUNYUAN,
}


# ============================================================
# 4. 服务器配置
# ============================================================

SERVER_HOST: str = _get_str("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = _get_int("SERVER_PORT", 8000)
SERVER_WORKERS: int = _get_int("SERVER_WORKERS", 1)
SERVER_TIMEOUT: int = _get_int("SERVER_TIMEOUT", 300)


# ============================================================
# 5. 数据与上传路径
# ============================================================

DATABASE_PATH: Path = _resolve_path(_get_str("DATABASE_PATH", "./data/app.db"))
UPLOAD_DIR: Path = _resolve_path(_get_str("UPLOAD_DIR", "./data/uploads"))
LOG_DIR: Path = _resolve_path("./logs")

MAX_FILE_SIZE_MB: int = _get_int("MAX_FILE_SIZE_MB", 10)


# ============================================================
# 6. 目录自动创建（幂等）
# ============================================================

def ensure_directories() -> None:
    """
    确保运行时所需目录存在。
    - DATABASE_PATH 的父目录
    - UPLOAD_DIR
    - LOG_DIR
    幂等操作：多次调用安全。
    """
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# 模块加载时自动执行（满足"程序启动时自动创建"要求）
ensure_directories()


# ============================================================
# 7. 启动自检与密钥脱敏
# ============================================================

def mask_secret(value: str) -> str:
    """密钥脱敏，用于日志打印。"""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}****{value[-4:]}"


def validate_config() -> List[str]:
    """
    启动时自检，返回警告列表（不抛异常，避免因漏配崩溃）。
    - 缺失的密钥会被标记为 warning
    - 由 main.py 决定是否打印/阻断
    """
    warnings: List[str] = []
    if not RELAY.api_key:
        warnings.append("RELAY_API_KEY 未配置（中转渠道 4 个模型不可用）")
    if not DEEPSEEK.api_key:
        warnings.append("DEEPSEEK_API_KEY 未配置")
    if not ZHIPU.api_key:
        warnings.append("ZHIPU_API_KEY 未配置")
    if not WENXIN.api_key:
        warnings.append("WENXIN_API_KEY 未配置")
    elif not WENXIN.api_key.strip().startswith("bce-v3/") and not WENXIN.secret_key:
        # bce-v3 版密钥自带签名，无需再配 Secret Key
        warnings.append("WENXIN_SECRET_KEY 未配置（非 bce-v3 密钥需 AK + SK）")
    if not KIMI.api_key:
        warnings.append("KIMI_API_KEY 未配置")
    return warnings
