"""aiogram polling and the local health reply. This is the only module that calls Bot."""

from collections.abc import Sequence

from peyda_pm_bot.adapters.telegram.mapping import to_incoming
from peyda_pm_bot.application.conversations.ports import (
    DeliveryRejected,
    DeliveryRetry,
    DeliveryUnknown,
)
from peyda_pm_bot.domain.conversations.models import IncomingUpdate

_ALLOWED = ["message", "edited_message"]
_HEALTH_TEXT = "ok"


class AiogramTelegram:
    """Update source and health sender around one aiogram Bot."""

    def __init__(self, bot: object) -> None:
        self._bot = bot

    async def fetch(self, offset: int | None) -> Sequence[IncomingUpdate]:
        get_updates = getattr(self._bot, "get_updates", None)
        raw_updates = await _invoke(
            get_updates,
            offset=offset,
            timeout=10,
            limit=100,
            allowed_updates=_ALLOWED,
        )
        if not isinstance(raw_updates, list):
            raise DeliveryUnknown("getUpdates did not return a list")
        return [to_incoming(item) for item in raw_updates]

    async def send_health(self, chat_id: int, topic_id: int) -> int:
        send_message = getattr(self._bot, "send_message", None)
        try:
            sent = await _invoke(
                send_message,
                chat_id=chat_id,
                text=_HEALTH_TEXT,
                message_thread_id=topic_id,
            )
        except Exception as exc:
            raise _classify(exc) from exc
        message_id = getattr(sent, "message_id", None)
        if not isinstance(message_id, int):
            raise DeliveryUnknown("health send returned no message id")
        return message_id

    async def close(self) -> None:
        session = getattr(self._bot, "session", None)
        close = getattr(session, "close", None)
        if callable(close):
            await _invoke(close)


async def _invoke(method: object, /, **kwargs: object) -> object:
    """Call one aiogram method. aiogram 3.32.0 does not ship py.typed."""

    if not callable(method):
        raise RuntimeError("telegram method is missing")
    return await method(**kwargs)


def _classify(exc: Exception) -> Exception:
    name = type(exc).__name__
    if name == "TelegramRetryAfter":
        retry_after = getattr(exc, "retry_after", 1)
        seconds = retry_after if isinstance(retry_after, int) else 1
        return DeliveryRetry(seconds)
    if name in {"TelegramNetworkError", "TelegramServerError", "TelegramEntityTooLarge"}:
        return DeliveryUnknown(name)
    if name in {
        "TelegramBadRequest",
        "TelegramForbiddenError",
        "TelegramUnauthorizedError",
        "TelegramNotFound",
        "TelegramConflictError",
    }:
        return DeliveryRejected(name)
    return DeliveryUnknown(name)
