"""Telegram updates become typed application values, and health errors stay classified."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from peyda_pm_bot.adapters.telegram.client import AiogramTelegram
from peyda_pm_bot.adapters.telegram.mapping import to_incoming
from peyda_pm_bot.application.conversations.ports import (
    DeliveryRejected,
    DeliveryRetry,
    DeliveryUnknown,
)
from peyda_pm_bot.domain.conversations.models import UpdateKind


def test_message_fields_are_copied_and_naive_times_become_utc() -> None:
    message = SimpleNamespace(
        message_id=10,
        date=datetime(2026, 11, 21, 12, 0),
        edit_date=None,
        text="hello",
        message_thread_id=7,
        from_user=SimpleNamespace(id=5),
        chat=SimpleNamespace(id=-100),
        reply_to_message=SimpleNamespace(message_id=3),
    )
    incoming = to_incoming(SimpleNamespace(update_id=4, message=message, edited_message=None))
    assert incoming.kind is UpdateKind.MESSAGE
    assert incoming.chat_id == -100
    assert incoming.topic_id == 7
    assert incoming.sender_id == 5
    assert incoming.reply_to_message_id == 3
    assert incoming.text == "hello"
    assert incoming.message_at == datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def test_other_updates_keep_no_text() -> None:
    incoming = to_incoming(SimpleNamespace(update_id=6, message=None, edited_message=None))
    assert incoming.kind is UpdateKind.OTHER
    assert incoming.text is None
    assert incoming.chat_id is None


def test_missing_update_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="update_id"):
        to_incoming(SimpleNamespace(message=None))


def test_real_aiogram_models_map() -> None:
    from aiogram.types import Chat, Message, Update, User

    moment = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    message = Message(
        message_id=10,
        date=moment,
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=5, is_bot=False, first_name="A"),
        text="hello",
        message_thread_id=7,
    )
    incoming = to_incoming(Update(update_id=4, message=message))
    assert incoming.kind is UpdateKind.MESSAGE
    assert incoming.chat_id == -100
    assert incoming.topic_id == 7
    assert incoming.sender_id == 5
    assert incoming.text == "hello"
    edited = message.model_copy(update={"text": "edited"})
    edited_update = to_incoming(Update(update_id=5, edited_message=edited))
    assert edited_update.kind is UpdateKind.EDITED_MESSAGE
    assert edited_update.text == "edited"


class _Updates:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.kwargs: dict[str, object] = {}

    async def get_updates(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.payload


class _Sender:
    def __init__(self, error: Exception | None = None, message_id: object = 15) -> None:
        self.error = error
        self.message_id = message_id
        self.kwargs: dict[str, object] = {}

    async def send_message(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(message_id=self.message_id)


class TelegramRetryAfter(Exception):
    def __init__(self) -> None:
        super().__init__("later")
        self.retry_after = 3


class TelegramNetworkError(Exception):
    pass


class TelegramBadRequest(Exception):
    pass


async def test_fetch_uses_the_phase_one_poll_arguments() -> None:
    bot = _Updates([SimpleNamespace(update_id=1, message=None, edited_message=None)])
    updates = await AiogramTelegram(bot).fetch(None)
    assert bot.kwargs == {
        "offset": None,
        "timeout": 10,
        "limit": 100,
        "allowed_updates": ["message", "edited_message"],
    }
    assert [item.update_id for item in updates] == [1]


async def test_fetch_rejects_a_non_list() -> None:
    with pytest.raises(DeliveryUnknown):
        await AiogramTelegram(_Updates(None)).fetch(6)


async def test_health_errors_are_classified_without_their_text() -> None:
    retry = AiogramTelegram(_Sender(TelegramRetryAfter()))
    with pytest.raises(DeliveryRetry) as caught:
        await retry.send_health(-100, 7)
    assert caught.value.retry_after_seconds == 3
    unknown = AiogramTelegram(
        _Sender(TelegramNetworkError("request https://example.invalid/secret"))
    )
    with pytest.raises(DeliveryUnknown) as unknown_caught:
        await unknown.send_health(-100, 7)
    assert "secret" not in str(unknown_caught.value)
    rejected = AiogramTelegram(_Sender(TelegramBadRequest()))
    with pytest.raises(DeliveryRejected):
        await rejected.send_health(-100, 7)
    sent = _Sender()
    assert await AiogramTelegram(sent).send_health(-100, 7) == 15
    assert sent.kwargs["text"] == "ok"
    assert sent.kwargs["chat_id"] == -100
    assert sent.kwargs["message_thread_id"] == 7
