"""Framework-independent message evidence and delivery data."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MessageEvent:
    chat_id: int
    topic_id: int | None
    message_id: int
    speaker_id: int | None
    speaker_name: str | None
    sender_chat_id: int | None
    reply_to_id: int | None
    text: str | None
    sent_at: datetime
    edited_at: datetime | None
    is_edit: bool


@dataclass(frozen=True)
class IncomingUpdate:
    update_id: int
    kind: str
    message: MessageEvent | None


@dataclass(frozen=True)
class Delivery:
    id: int
    chat_id: int
    topic_id: int
    reply_to_id: int | None
    text: str
    attempts: int
    target_id: int | None = None
    operation: str = "message"
    reaction_emoji: str | None = None
    rich_text: bool = False
