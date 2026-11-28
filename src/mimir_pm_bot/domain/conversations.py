from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Version:
    chat: int
    topic: int
    message: int
    speaker_kind: str
    speaker_id: int | None
    speaker_name: str | None
    text: str | None
    caption: str | None
    sent: datetime
    edited: datetime | None
    reply_to: int | None
    is_edit: bool


@dataclass(frozen=True)
class Event:
    update_id: int
    arrived: datetime
    version: Version | None
    admin: tuple[int, str] | None = None
