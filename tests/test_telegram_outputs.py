"""One turn may emit multiple bounded, independently targeted Telegram operations."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendRichMessage, SetMessageReaction
from aiogram.types import InputRichMessage
from conftest import event
from pydantic import ValidationError
from sqlalchemy import select
from test_phase_two import batch

from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent, ResultPayload, decode_reply
from peyda_pm_bot.adapters.storage.schema import jobs, outbox
from peyda_pm_bot.adapters.telegram.aiogram_transport import AiogramTransport
from peyda_pm_bot.adapters.telegram.fake import FakeTransport
from peyda_pm_bot.application.ports import PermanentFailure, TemporaryFailure, UnknownOutcome
from peyda_pm_bot.domain.agent.models import AgentResult, SendMessage, SetReaction
from peyda_pm_bot.domain.conversations.events import Delivery
from peyda_pm_bot.runtime.delivery import Sender
from peyda_pm_bot.runtime.process import ProcessOwner


async def make_request(database, conversations):
    await batch(
        database,
        conversations,
        [event(text="first"), event(11, message_id=101, speaker_id=8, text="second")],
    )
    async with database.unit() as unit:
        return await unit.queue.claim(105)


async def test_multiple_replies_plain_message_and_reaction(database, conversations):
    request = await make_request(database, conversations)
    result = AgentResult(
        "reply",
        actions=(
            SendMessage("answer one", 100),
            SetReaction(101, "👍"),
            SendMessage("answer two", 101),
            SendMessage("summary"),
        ),
    )
    async with database.unit() as unit:
        assert await unit.queue.complete(request, result)
    transport = FakeTransport()
    sender = Sender(database.unit, transport)
    while await sender.send_one():
        pass
    messages = [item for item in transport.sent if item.operation == "message"]
    assert [item.reply_to_id for item in messages] == [100, 101, None]
    assert [item.text for item in messages] == ["answer one", "answer two", "summary"]
    assert transport.reactions[(-1001, 101)] == "👍"
    async with database.unit() as unit:
        assert not await unit.queue.complete(request, result)
        saved = json.loads(await unit.connection.scalar(select(jobs.c.result)))
        assert saved["actions"][1] == {"kind": "reaction", "message_id": 101, "emoji": "👍"}


async def test_reaction_only_and_clear(database, conversations):
    request = await make_request(database, conversations)
    async with database.unit() as unit:
        await unit.queue.complete(request, AgentResult("reply", actions=(SetReaction(100, None),)))
    transport = FakeTransport()
    assert await Sender(database.unit, transport).send_one()
    assert transport.sent[0].operation == "reaction"
    assert transport.reactions[(-1001, 100)] is None


@pytest.mark.parametrize(
    "result",
    [
        AgentResult("reply", actions=(SendMessage("valid", 100), SetReaction(999, "👍"))),
        AgentResult("reply", actions=(SendMessage("valid", 100), SendMessage("bad", 999))),
        AgentResult("reply", actions=(SendMessage(" "),)),
        AgentResult("silent", actions=(SetReaction(100, "👍"),)),
        AgentResult("reply", "legacy", actions=(SendMessage("mixed"),)),
        AgentResult("reply", actions=(SetReaction(100, "👍"), SetReaction(100, "❤"))),
        AgentResult("reply", actions=(SetReaction(100, ""),)),
        AgentResult("reply", actions=tuple(SendMessage("spam") for _ in range(21))),
    ],
)
async def test_invalid_output_rejects_entire_list(database, conversations, result):
    request = await make_request(database, conversations)
    with pytest.raises(ValueError):
        async with database.unit() as unit:
            await unit.queue.complete(request, result)
    async with database.unit() as unit:
        assert (await unit.connection.execute(select(outbox))).first() is None
        assert await unit.connection.scalar(select(jobs.c.status)) == "running"


async def test_failed_reaction_does_not_block_text(database, conversations):
    request = await make_request(database, conversations)
    async with database.unit() as unit:
        await unit.queue.complete(
            request,
            AgentResult("reply", actions=(SetReaction(100, "👍"), SendMessage("answer", 101))),
        )
    transport = FakeTransport()
    transport.send_errors.append(PermanentFailure())
    sender = Sender(database.unit, transport)
    assert await sender.send_one()
    assert await sender.send_one()
    assert transport.sent[0].text == "answer"


async def test_long_messages_preserve_logical_order(database, conversations):
    request = await make_request(database, conversations)
    async with database.unit() as unit:
        await unit.queue.complete(
            request,
            AgentResult("reply", actions=(SendMessage("a" * 3501, 100), SendMessage("next", 101))),
        )
    transport = FakeTransport()
    sender = Sender(database.unit, transport)
    while await sender.send_one():
        pass
    assert [(len(item.text), item.reply_to_id) for item in transport.sent] == [
        (3500, 100),
        (1, 100),
        (4, 101),
    ]


def test_typed_acp_action_schema_and_legacy_compatibility():
    payload = ResultPayload.model_validate_json(
        json.dumps(
            {
                "disposition": "reply",
                "actions": [
                    {"kind": "message", "text": "standalone", "reply_to_id": None},
                    {"kind": "reaction", "message_id": 100, "emoji": "👍"},
                ],
            }
        )
    )
    assert payload.to_result().actions == (SendMessage("standalone"), SetReaction(100, "👍"))
    assert (
        ResultPayload.model_validate_json('{"disposition":"reply","text":"legacy"}')
        .to_result()
        .text
        == "legacy"
    )
    for action in [
        {"kind": "reaction", "message_id": 100, "emoji": "👍", "chat_id": 999},
        {"kind": "reaction", "message_id": 100, "custom_emoji_id": "1"},
        {"kind": "delete", "message_id": 100},
    ]:
        with pytest.raises(ValidationError):
            ResultPayload.model_validate_json(
                json.dumps({"disposition": "reply", "actions": [action]})
            )


def test_markdown_and_fenced_structured_replies_are_distinct():
    text = "**گزارش**\n\n- یک مورد\n- دو مورد"
    assert decode_reply(text) == AgentResult("reply", text)
    assert decode_reply('```json\n{"disposition":"silent"}\n```') == AgentResult("silent")
    example = '```json\n{"example":42}\n```'
    assert decode_reply(example) == AgentResult("reply", example)
    for incomplete in ("  ", '{"disposition":"reply","actions":['):
        with pytest.raises(ValueError):
            decode_reply(incomplete)


async def test_acp_markdown_becomes_one_persisted_rich_topic_reply(
    database, conversations, tmp_path, monkeypatch
):
    request = await make_request(database, conversations)
    script = Path(__file__).parent / "fixtures" / "acp_server.py"
    owner = ProcessOwner([sys.executable, str(script), "--markdown-reply"], tmp_path)
    agent = AcpAgent(owner, tmp_path, validated_environment=True)
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    transport = AiogramTransport(bot)
    send = AsyncMock(return_value=type("Response", (), {"message_id": 20})())
    monkeypatch.setattr(bot, "send_rich_message", send)
    try:
        await agent.connect(None)
        results = [event async for event in agent.run(request)]
        assert send.await_count == 0
        result = results[-1]
        assert isinstance(result, AgentResult)
        async with database.unit() as unit:
            assert await unit.queue.complete(request, result)
        sender = Sender(database.unit, transport)
        assert await sender.send_one()
        assert not await sender.send_one()
        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        assert kwargs["message_thread_id"] == 42
        assert kwargs["reply_parameters"].message_id == 101
        assert kwargs["rich_message"].markdown == "**پاسخ**\n- جزئیات"
        assert kwargs["rich_message"].is_rtl
    finally:
        await transport.close()
        await agent.close()


@pytest.mark.parametrize("error_type", [TelegramBadRequest, TelegramNetworkError])
async def test_rich_reply_only_falls_back_for_explicit_rejection(monkeypatch, error_type):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    plain = AsyncMock(return_value=type("Response", (), {"message_id": 456})())
    request = SendRichMessage(chat_id=-1001, rich_message=InputRichMessage(markdown="**test**"))
    rich = AsyncMock(side_effect=error_type(method=request, message="test error"))
    monkeypatch.setattr(bot, "send_rich_message", rich)
    monkeypatch.setattr(bot, "send_message", plain)
    transport = AiogramTransport(bot)
    delivery = Delivery(1, -1001, 42, 30, "**test**", 1, rich_text=True)
    try:
        if error_type is TelegramNetworkError:
            with pytest.raises(UnknownOutcome):
                await transport.send(delivery)
            plain.assert_not_awaited()
        else:
            assert await transport.send(delivery) == 456
            assert plain.await_args.kwargs["text"] == "**test**"
            assert plain.await_args.kwargs["reply_parameters"].message_id == 30
        rich.assert_awaited_once()
    finally:
        await transport.close()


async def test_transport_replies_optional_and_reactions(monkeypatch):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    send = AsyncMock(return_value=type("Response", (), {"message_id": 9})())
    react = AsyncMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send)
    monkeypatch.setattr(bot, "set_message_reaction", react)
    transport = AiogramTransport(bot)
    try:
        await transport.send(Delivery(1, -1001, 42, None, "plain", 1))
        assert send.call_args.kwargs["reply_parameters"] is None
        await transport.send(Delivery(2, -1001, 42, 100, "reply", 1))
        assert send.call_args.kwargs["reply_parameters"].message_id == 100
        await transport.send(Delivery(3, -1001, 42, None, "", 1, 100, "reaction", "👍"))
        assert react.call_args.kwargs["reaction"][0].emoji == "👍"
        assert react.call_args.kwargs["message_id"] == 100
        await transport.send(Delivery(4, -1001, 42, None, "", 1, 100, "reaction", None))
        assert react.call_args.kwargs["reaction"] == []
        assert send.await_count == 2
    finally:
        await transport.close()


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            TelegramBadRequest(
                method=SetMessageReaction(chat_id=-1001, message_id=100), message="REACTION_INVALID"
            ),
            PermanentFailure,
        ),
        (
            TelegramNetworkError(
                method=SetMessageReaction(chat_id=-1001, message_id=100), message="offline"
            ),
            UnknownOutcome,
        ),
        (
            TelegramRetryAfter(
                method=SetMessageReaction(chat_id=-1001, message_id=100),
                message="limited",
                retry_after=5,
            ),
            TemporaryFailure,
        ),
    ],
)
async def test_reaction_errors_are_classified(monkeypatch, error, expected):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    monkeypatch.setattr(bot, "set_message_reaction", AsyncMock(side_effect=error))
    try:
        with pytest.raises(expected):
            await AiogramTransport(bot).send(
                Delivery(1, -1001, 42, None, "", 1, 100, "reaction", "👍")
            )
    finally:
        await bot.session.close()


async def test_restart_retries_remaining_message_without_resending_first(database, conversations):
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from peyda_pm_bot.adapters.storage.database import Database

    request = await make_request(database, conversations)
    async with database.unit() as unit:
        await unit.queue.complete(
            request,
            AgentResult("reply", actions=(SendMessage("first", 100), SendMessage("second", 101))),
        )
    now = datetime.now(UTC)
    transport = FakeTransport()
    sender = Sender(database.unit, transport, clock=lambda: now)
    assert await sender.send_one()
    transport.send_errors.append(TemporaryFailure())
    assert await sender.send_one()
    path = Path(database.engine.url.database)
    await database.close()
    restored = Database(path)
    try:
        await restored.initialize(path, "test")
        now += timedelta(seconds=2)
        retry_sender = Sender(restored.unit, transport, clock=lambda: now)
        assert await retry_sender.send_one()
        assert not await retry_sender.send_one()
        assert [item.text for item in transport.sent] == ["first", "second"]
    finally:
        await restored.close()


async def test_upgrade_preserves_phase_two_pending_output(tmp_path):
    import asyncio
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from peyda_pm_bot.adapters.storage.database import Database

    path = tmp_path / "old.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    await asyncio.to_thread(command.upgrade, config, "0002")

    def populate():
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO outbox (chat_id,topic_id,reply_to_id,text,status,delivery_key) "
                "VALUES (-1001,42,100,'old response','pending','old-key')"
            )

    await asyncio.to_thread(populate)
    database = Database(path)
    try:
        await database.initialize(path, "test")
        async with database.unit() as unit:
            row = (await unit.connection.execute(select(outbox))).mappings().one()
            assert (row["reply_to_id"], row["text"], row["operation"], row["delivery_key"]) == (
                100,
                "old response",
                "message",
                "old-key",
            )
    finally:
        await database.close()
