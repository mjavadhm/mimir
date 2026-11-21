"""Validated process settings. Constructing them does not read a dotenv file."""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from peyda_pm_bot.domain.conversations.models import Allowlist

_REQUIRED = ("PEYDA_BOT_TOKEN", "PEYDA_GROUP_ID", "PEYDA_TOPIC_ID", "PEYDA_ADMIN_IDS")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_token: str = Field(min_length=1)
    group_id: int
    topic_id: int = Field(gt=0)
    admin_ids: frozenset[int] = Field(min_length=1)
    database_path: Path
    agy_cli_path: Path | None = None

    @field_validator("group_id")
    @classmethod
    def group_is_set(cls, value: int) -> int:
        if value == 0:
            raise ValueError("group id must not be zero")
        return value

    @field_validator("admin_ids")
    @classmethod
    def positive_admins(cls, value: frozenset[int]) -> frozenset[int]:
        if any(item <= 0 for item in value):
            raise ValueError("admin ids must be positive")
        return value

    def allowlist(self) -> Allowlist:
        return Allowlist(group_id=self.group_id, topic_id=self.topic_id, admin_ids=self.admin_ids)


def settings_from_environ(database_path: Path) -> Settings:
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise ValueError("missing settings: " + ", ".join(missing))
    try:
        group_id = int(os.environ["PEYDA_GROUP_ID"])
        topic_id = int(os.environ["PEYDA_TOPIC_ID"])
    except ValueError as exc:
        raise ValueError("PEYDA_GROUP_ID and PEYDA_TOPIC_ID must be integers") from exc
    configured = os.environ.get("PEYDA_AGY_CLI")
    candidate = Path(configured) if configured else Path.home() / ".local" / "bin" / "agy-cli"
    agy_cli = candidate if candidate.is_file() else None
    return Settings(
        bot_token=os.environ["PEYDA_BOT_TOKEN"],
        group_id=group_id,
        topic_id=topic_id,
        admin_ids=_admin_ids(os.environ["PEYDA_ADMIN_IDS"]),
        database_path=database_path,
        agy_cli_path=agy_cli,
    )


def _admin_ids(raw: str) -> frozenset[int]:
    parts = [part.strip() for part in raw.strip().strip("[]").split(",") if part.strip()]
    if not parts:
        raise ValueError("PEYDA_ADMIN_IDS must list positive integers")
    try:
        values = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("PEYDA_ADMIN_IDS must list positive integers") from exc
    if any(value <= 0 for value in values):
        raise ValueError("PEYDA_ADMIN_IDS must list positive integers")
    return frozenset(values)
