"""Typed conversation facts that do not depend on Telegram or the database."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEALTH_TEXT = "/health"


class UpdateKind(StrEnum):
    MESSAGE = "message"
    EDITED_MESSAGE = "edited_message"
    OTHER = "other"


class Admission(StrEnum):
    ACCEPTED = "accepted"
    IGNORED = "ignored"


class Allowlist(BaseModel):
    """The only group, topic, and admins that may affect product state."""

    model_config = ConfigDict(frozen=True)

    group_id: int
    topic_id: int = Field(gt=0)
    admin_ids: frozenset[int]

    @field_validator("group_id")
    @classmethod
    def group_is_set(cls, value: int) -> int:
        if value == 0:
            raise ValueError("group id must not be zero")
        return value

    def accepts_admin(self, sender_id: int) -> bool:
        return sender_id in self.admin_ids


class IncomingUpdate(BaseModel):
    """One Telegram update after it has been mapped out of the SDK models."""

    model_config = ConfigDict(frozen=True)

    update_id: int = Field(ge=0)
    kind: UpdateKind
    chat_id: int | None = None
    topic_id: int | None = None
    message_id: int | None = None
    sender_id: int | None = None
    reply_to_message_id: int | None = None
    text: str | None = None
    message_at: datetime | None = None
    edited_at: datetime | None = None


class AdmissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    admission: Admission
    reason: str | None = None
