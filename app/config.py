from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(..., alias="BOT_TOKEN")
    database_url: str = Field(..., alias="DATABASE_URL")
    special_database_url: str | None = Field(default=None, alias="SPECIAL_DATABASE_URL")
    special_group_id: int = Field(default=-1001151404251, alias="SPECIAL_GROUP_ID")
    admin_ids: List[int] = Field(default_factory=list, alias="ADMIN_IDS")
    dev_admin_ids: List[int] = Field(default_factory=list, alias="DEV_ADMIN_IDS")
    bot_username: Optional[str] = Field(default=None, alias="BOT_USERNAME")
    delete_unregistered_user_message: bool = Field(
        default=False,
        alias="DELETE_UNREGISTERED_USER_MESSAGE",
    )
    registration_timeout_seconds: int = Field(
        default=600,
        alias="REGISTRATION_TIMEOUT_SECONDS",
    )
    group_reply_cooldown_seconds: int = Field(
        default=30,
        alias="GROUP_REPLY_COOLDOWN_SECONDS",
    )
    nsfw_scan_on_join: bool = Field(default=True, alias="NSFW_SCAN_ON_JOIN")
    nsfw_profile_threshold: float = Field(default=0.8, alias="NSFW_PROFILE_THRESHOLD")
    nsfw_model_dir: str = Field(default="models/open_nsfw", alias="NSFW_MODEL_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file: str = Field(default="logs/bot.log", alias="LOG_FILE")
    max_purpose_length: int = Field(default=200, alias="MAX_PURPOSE_LENGTH")
    allowed_group_id: int | None = Field(default=None, alias="ALLOWED_GROUP_ID")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("admin_ids", "dev_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> List[int]:
        if value is None or value == "":
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, list):
            return [int(item) for item in value]
        if isinstance(value, str):
            cleaned = [item.strip() for item in value.split(",") if item.strip()]
            return [int(item) for item in cleaned]
        raise ValueError("ADMIN_IDS must be a comma-separated string or list")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
