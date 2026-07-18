"""Application settings loaded from .env"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MySQL
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "t_bot"

    # Telegram
    telegram_bot_token: str = ""
    public_base_url: str = ""  # e.g. https://xxx.ngrok-free.app

    # Gemini (free via Google AI Studio)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    # How many recent chat messages (user+assistant) to replay as context.
    # 0 disables short-term memory (DB state still grounds the agent).
    history_limit: int = 20

    # Optional: OpenAI Whisper for voice (Gemini can also do audio)
    openai_api_key: str = ""
    stt_provider: str = "gemini"  # gemini | openai | none

    # App
    app_env: str = "development"
    generated_dir: str = "generated"
    log_dir: str = "logs"
    log_retention_days: int = 30
    # local = Telegram long-polling (no PUBLIC_BASE_URL needed)
    # webhook = needs PUBLIC_BASE_URL
    telegram_mode: str = "polling"

    # Scheduled reports (set per-chat via conversation; stored in preferences)
    scheduler_enabled: bool = True
    report_timezone: str = "Asia/Kolkata"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
