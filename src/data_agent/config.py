"""Configuration management for Data Agent."""

import os
from dataclasses import dataclass, field
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # MatrixOne
    mo_host: str = field(default_factory=lambda: os.getenv("MO_HOST", "127.0.0.1"))
    mo_port: int = field(default_factory=lambda: int(os.getenv("MO_PORT", "6001")))
    mo_user: str = field(default_factory=lambda: os.getenv("MO_USER", "root"))
    mo_password: str = field(default_factory=lambda: os.getenv("MO_PASSWORD", "111"))
    mo_database: str = field(default_factory=lambda: os.getenv("MO_DATABASE", "data_agent"))

    # MOI
    moi_base_url: str = field(default_factory=lambda: os.getenv("MOI_BASE_URL", ""))
    moi_api_key: str = field(default_factory=lambda: os.getenv("MOI_API_KEY", ""))

    # LLM (DeepSeek, OpenAI-compatible)
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))

    # App
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    upload_max_size_mb: int = field(default_factory=lambda: int(os.getenv("UPLOAD_MAX_SIZE_MB", "50")))
    upload_dir: str = field(default_factory=lambda: os.getenv("UPLOAD_DIR", "data/uploads"))

    @property
    def mo_connection_url(self) -> str:
        user = quote_plus(self.mo_user)
        password = quote_plus(self.mo_password)
        return (
            f"mysql+pymysql://{user}:{password}"
            f"@{self.mo_host}:{self.mo_port}/{self.mo_database}"
        )


settings = Settings()
