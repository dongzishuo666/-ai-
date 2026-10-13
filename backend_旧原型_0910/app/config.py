"""全局配置：统一读取环境变量，适配 2GB 轻量部署。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """极简 .env 加载器（避免额外依赖）：不覆盖已存在的环境变量。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """进程级单例配置。"""

    def __init__(self) -> None:
        self.app_name = "多AI协助系统 · MVP"
        self.version = "0.1.0"

        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8000"))

        self.data_dir = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
        self.db_path = Path(os.getenv("DB_PATH", str(self.data_dir / "app.db")))

        # 强制模拟模式：无任何 API Key 也能完整演示三大功能
        self.force_mock = _env_bool("MOCK_MODE", False)

        self.request_timeout = float(os.getenv("REQUEST_TIMEOUT", "60"))
        self.max_retries = int(os.getenv("MAX_RETRIES", "1"))
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "20"))
        self.max_doc_chars = int(os.getenv("MAX_DOC_CHARS", "6000"))

        origins = os.getenv("CORS_ORIGINS", "*")
        self.cors_origins = [o.strip() for o in origins.split(",") if o.strip()] or ["*"]

        # 渠道聚合网关
        self.relay_base_url = os.getenv("RELAY_BASE_URL", "").rstrip("/")
        self.relay_api_key = os.getenv("RELAY_API_KEY", "")

        self.data_dir.mkdir(parents=True, exist_ok=True)

    def env(self, key: str, default: str = "") -> str:
        return os.getenv(key, default)


settings = Settings()
