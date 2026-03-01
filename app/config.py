"""
Centralised configuration loaded from environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_env_file = os.getenv("ENV_FILE", ".env")
load_dotenv(_env_file)


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    MINIMAX_API_KEY: str = os.getenv("MINIMAX_API_KEY", "")
    SOCIALDATA_API_KEY: str = os.getenv("SOCIALDATA_API_KEY", "")
    SOCIALDATA_BASE_URL: str = os.getenv("SOCIALDATA_BASE_URL", "https://api.socialdata.tools")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")
    MINIMAX_BASE_URL: str = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    MINIMAX_CHAT_PATH: str = os.getenv("MINIMAX_CHAT_PATH", "/text/chatcompletion_v2")
    SCORING_MODEL: str = os.getenv("SCORING_MODEL", "openai/gpt-4o")
    SCORING_MODE: str = os.getenv("SCORING_MODE", "llm")

    MAIN_X_HANDLE: str = os.getenv("MAIN_X_HANDLE", "")
    METRICS_MODE: str = os.getenv("METRICS_MODE", "manual")
    CLASSIFICATION_MODE: str = os.getenv("CLASSIFICATION_MODE", "rules")
    AUTO_CREATE_PROJECTS: bool = os.getenv("AUTO_CREATE_PROJECTS", "true").lower() in {"1", "true", "yes", "on"}

    INGEST_SHARED_SECRET: str = os.getenv("INGEST_SHARED_SECRET", "change-me")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", str(Path("logs")))

    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Jerusalem")

    SCORING_DELAY_HOURS: int = int(os.getenv("SCORING_DELAY_HOURS", "48"))
    WORKER_POLL_SECONDS: int = int(os.getenv("WORKER_POLL_SECONDS", "300"))


settings = Settings()
