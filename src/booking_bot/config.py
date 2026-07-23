from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Telegram Booking Platform"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://booking:booking@localhost:55432/booking"
    redis_url: str = "redis://localhost:6379/0"
    telegram_webhook_base_url: AnyHttpUrl | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_proxy_url: SecretStr | None = None
    telegram_webhook_header_secret: SecretStr | None = None
    bot_token_encryption_key: SecretStr | None = None
    booking_horizon_days: int = 60
    booking_min_lead_hours: int = 3
    slot_hold_minutes: int = 10
    cancellation_cutoff_hours: int = 24
    booking_dates_shown: int = 14
    notification_poll_interval_seconds: float = 2.0
    notification_batch_size: int = 20
    notification_max_attempts: int = 5

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
