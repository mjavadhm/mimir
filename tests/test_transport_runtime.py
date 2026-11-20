import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates, SendMessage
from aiogram.types import Update
from conftest import event
from pydantic import ValidationError

from peyda_pm_bot.adapters.telegram.aiogram_transport import AiogramTransport, normalize
from peyda_pm_bot.adapters.telegram.fake import FakeTransport
from peyda_pm_bot.application.ports import TemporaryFailure, UnknownOutcome
from peyda_pm_bot.config import Settings
from peyda_pm_bot.domain.conversations.events import Delivery
from peyda_pm_bot.runtime.delivery import Sender
from peyda_pm_bot.runtime.instance import instance_lock
from peyda_pm_bot.runtime.runner import run


def test_normalization_preserves_anonymous_sender_caption_and_reply():
    raw = Update.model_validate(
        {
            "update_id": 9,
            "edited_message": {
                "message_id": 30,
                "date": 1794873600,
                "edit_date": 1794873700,
                "chat": {"id": -1001, "type": "supergroup", "title": "test"},
                "sender_chat": {"id": -1001, "type": "supergroup", "title": "test"},
                "message_thread_id": 42,
                "caption": "edited caption",
                "reply_to_message": {
                    "message_id": 29,
                    "date": 1794873600,
                    "chat": {"id": -1001, "type": "supergroup", "title": "test"},
                },
            },
        }
    )
    message = normalize(raw).message
    assert message.sender_chat_id == -1001
    assert message.speaker_id is None
    assert message.text == "edited caption"
    assert message.reply_to_id == 29
    assert message.is_edit
    assert message.sent_at.utcoffset().total_seconds() == 0
    assert normalize(Update(update_id=10)).message is None


async def test_aiogram_uses_durable_offset_and_topic_reply(monkeypatch):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    transport = AiogramTransport(bot)
    receive = AsyncMock(return_value=[])
    sent = AsyncMock(return_value=type("Response", (), {"message_id": 200})())
    try:
        monkeypatch.setattr(bot, "get_updates", receive)
        monkeypatch.setattr(bot, "send_message", sent)
        assert await transport.receive(123) == []
        assert receive.call_args.kwargs["offset"] == 123
        assert await transport.send(Delivery(1, -1001, 42, 30, "health", 1)) == 200
        assert sent.call_args.kwargs["message_thread_id"] == 42
        assert sent.call_args.kwargs["reply_parameters"].message_id == 30
    finally:
        await transport.close()


async def test_aiogram_network_send_is_unknown_and_receive_is_retryable(monkeypatch):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    transport = AiogramTransport(bot)
    try:
        monkeypatch.setattr(
            bot,
            "get_updates",
            AsyncMock(side_effect=TelegramNetworkError(method=GetUpdates(), message="offline")),
        )
        monkeypatch.setattr(
            bot,
            "send_message",
            AsyncMock(
                side_effect=TelegramNetworkError(
                    method=SendMessage(chat_id=-1001, text="test"), message="offline"
                )
            ),
        )
        with pytest.raises(TemporaryFailure):
            await transport.receive(1)
        with pytest.raises(UnknownOutcome):
            await transport.send(Delivery(1, -1001, 42, 30, "health", 1))
    finally:
        await transport.close()


async def test_runtime_ingests_during_blocked_send_and_shutdown_closes(database, conversations):
    class BlockingTransport(FakeTransport):
        async def receive(self, offset):
            if offset >= 12:
                ingested.set()
            return await super().receive(offset)

        async def send(self, delivery):
            sending.set()
            await asyncio.Event().wait()

    sending = asyncio.Event()
    ingested = asyncio.Event()
    transport = BlockingTransport([event()])
    stop = asyncio.Event()
    task = asyncio.create_task(
        run(conversations, Sender(database.unit, transport), transport, stop)
    )
    try:
        await asyncio.wait_for(sending.wait(), timeout=5)
        transport.updates.append(event(11, text="new input"))
        await asyncio.wait_for(ingested.wait(), timeout=5)
        assert not task.done()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    assert transport.closed
    assert all("run.<locals>" not in item.get_coro().__qualname__ for item in asyncio.all_tasks())


def test_second_instance_is_rejected_and_lock_released(tmp_path):
    path = tmp_path / "instance.db"
    with instance_lock(path):
        with pytest.raises(RuntimeError, match="Another bot"):
            with instance_lock(path):
                pass
    with instance_lock(path):
        pass


def test_settings_validate_without_real_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        project_id="test",
        group_id=-1001,
        topic_id=42,
        admin_ids={7},
        database_path=tmp_path / "test.db",
    )
    assert settings.bot_token is None
    with pytest.raises(ValidationError):
        Settings(
            project_id="test", group_id=-1001, topic_id=42, admin_ids=set(), database_path="test.db"
        )


async def test_application_startup_failure_closes_transport(tmp_path):
    from peyda_pm_bot.adapters.storage.database import Database
    from peyda_pm_bot.bootstrap import application

    path = tmp_path / "bound.db"
    database = Database(path)
    try:
        await database.initialize(path, "first-project")
    finally:
        await database.close()
    settings = Settings(
        project_id="different-project",
        group_id=-1001,
        topic_id=42,
        admin_ids={7},
        database_path=path,
    )
    transport = FakeTransport()
    with pytest.raises(ValueError, match="another project"):
        async with application(settings, transport):
            pytest.fail("Must not start with another project's database")
    assert transport.closed


async def test_receive_retry_budget_and_retry_after(database, conversations, monkeypatch):
    from peyda_pm_bot.runtime import runner

    waits = []

    async def record_pause(stop, seconds):
        if seconds >= 1:
            waits.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(runner, "pause", record_pause)
    transport = FakeTransport()
    transport.receive_errors.extend([TemporaryFailure(60) for _ in range(6)])
    with pytest.raises(ExceptionGroup) as error:
        await run(conversations, Sender(database.unit, transport), transport, asyncio.Event())
    assert "Receive failed six consecutive times" in str(error.value.exceptions[0])
    assert waits == [60] * 5
    assert transport.closed
    assert await conversations.offset() == 0
