"""Map aiogram updates into application updates. Values are checked before use."""

from datetime import UTC, datetime

from peyda_pm_bot.domain.conversations.models import IncomingUpdate, UpdateKind


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return None


def _message_update(update_id: int, message: object, kind: UpdateKind) -> IncomingUpdate:
    sender = getattr(message, "from_user", None)
    reply = getattr(message, "reply_to_message", None)
    chat = getattr(message, "chat", None)
    return IncomingUpdate(
        update_id=update_id,
        kind=kind,
        chat_id=_optional_int(getattr(chat, "id", None)),
        topic_id=_optional_int(getattr(message, "message_thread_id", None)),
        message_id=_optional_int(getattr(message, "message_id", None)),
        sender_id=_optional_int(getattr(sender, "id", None)),
        reply_to_message_id=_optional_int(getattr(reply, "message_id", None)),
        text=_optional_str(getattr(message, "text", None)),
        message_at=_optional_datetime(getattr(message, "date", None)),
        edited_at=_optional_datetime(getattr(message, "edit_date", None)),
    )


def to_incoming(update: object) -> IncomingUpdate:
    """Copy the fields ingest needs. Other update types carry no message text."""

    update_id = _optional_int(getattr(update, "update_id", None))
    if update_id is None:
        raise ValueError("telegram update has no update_id")
    message = getattr(update, "message", None)
    if message is not None:
        return _message_update(update_id, message, UpdateKind.MESSAGE)
    edited = getattr(update, "edited_message", None)
    if edited is not None:
        return _message_update(update_id, edited, UpdateKind.EDITED_MESSAGE)
    return IncomingUpdate(update_id=update_id, kind=UpdateKind.OTHER)
