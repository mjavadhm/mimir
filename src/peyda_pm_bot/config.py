"""Validated local configuration; no I/O occurs on import."""

from datetime import time
from pathlib import Path
from typing import Annotated, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    """Credentials-only configuration for discovery before chat IDs are known."""

    model_config = SettingsConfigDict(env_prefix="PEYDA_", env_file=".env", extra="ignore")

    bot_token: SecretStr | None = None


class Settings(TelegramSettings):
    model_config = SettingsConfigDict(env_prefix="PEYDA_", env_file=".env", extra="forbid")

    project_id: str = Field(min_length=1)
    group_id: int = Field(lt=0)
    topic_id: int = Field(gt=0)
    admin_ids: frozenset[Annotated[int, Field(gt=0)]] = Field(min_length=1)
    database_path: Path
    max_send_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_seconds: float = Field(default=1, gt=0, le=60)

    batch_quiet_seconds: float = Field(default=4, gt=0, le=60)
    batch_ceiling_seconds: float = Field(default=12, gt=0, le=300)
    agent_timeout_seconds: float = Field(default=600, gt=0, le=3600)
    agent_startup_seconds: float = Field(default=30, gt=0, le=300)
    agent_cancel_seconds: float = Field(default=5, gt=0, le=60)

    memory_policy_path: Path | None = None
    antigravity_cli_path: Path = Path("agy-cli")
    agent_workspace_path: Path | None = None
    agent_conversation_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    agent_desktop_keyring: bool = False
    agent_turbo: bool = True

    github_repository: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )
    github_token: SecretStr | None = None
    github_bot_login: str | None = None
    github_live_validated: bool = False
    member_logins: dict[int, str] = Field(default_factory=dict)
    status_labels: dict[str, str] = Field(
        default_factory=lambda: {
            state: f"pm:{state}"
            for state in ("backlog", "ready", "in_progress", "in_review", "blocked", "done")
        }
    )
    report_timezone: str = "Asia/Tehran"
    report_at: time | None = None
    reminder_at: time | None = None
    github_sync_seconds: float = Field(default=900, ge=60, le=86400)
    idle_work_hours: float = Field(default=72, gt=0, le=8760)

    @field_validator("report_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Expected an IANA time zone") from error
        return value

    @field_validator("report_at", "reminder_at")
    @classmethod
    def validate_daily_time(cls, value: time | None) -> time | None:
        if value is not None and (value.tzinfo is not None or value.second or value.microsecond):
            raise ValueError("Expected a local time in HH:MM format")
        return value

    @model_validator(mode="after")
    def validate_github_mapping(self) -> Self:
        expected = {"backlog", "ready", "in_progress", "in_review", "blocked", "done"}
        if (
            set(self.status_labels) != expected
            or len(set(self.status_labels.values())) != 6
            or any(not label.strip() for label in self.status_labels.values())
        ):
            raise ValueError("Status labels must map six states to distinct nonempty names")
        return self
