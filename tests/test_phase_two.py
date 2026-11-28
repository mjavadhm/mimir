import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import Update

from mimir_pm_bot.adapters.outputs import Outputs
from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.telegram import normalize
from mimir_pm_bot.adapters.turn_store import Turns
from mimir_pm_bot.domain.conversations import Event, Version


@pytest.fixture
def queue(tmp_path):
    path = tmp_path / "turns.db"
    db = Store(path)
    db.initialize(12, -91, 6)
    now = datetime.now(UTC) - timedelta(seconds=30)
    v = Version(-91, 6, 5, "user", 7, "test", "hello", None, now, None, None, False)
    db.record(Event(11, now, v))
    return Turns(path)


def test_worker_exclusive_and_uncertain_recovery(queue):
    assert queue.claim(8) is None
    assert "exceeds" in queue.status()
    t = queue.claim(4000)
    assert '"version": 11' in t.prompt
    assert queue.claim(4000) is None
    queue.stage(t.pile, "key", "send", ["first", "second"], None)
    assert queue.next_output().payload == "first"
    reboot = Turns(queue.path)
    reboot.recover()
    now = datetime.now(UTC) - timedelta(seconds=10)
    Store(queue.path).record(
        Event(12, now, Version(-91, 6, 6, "user", 7, "test", "next", None, now, None, None, False))
    )
    following = reboot.claim(4000)
    assert following is not None and following.pile != t.pile
    assert '"version": 12' in following.prompt
    assert reboot.claim(4000) is None
    assert reboot.next_output() is None
    assert reboot.stage(t.pile, "key", "send", ["first", "second"], None) == "unknown, failed"


def test_revalidate_before_network(queue):
    turn = queue.claim(4000)
    queue.stage(turn.pile, "reply", "reply", ["answer"], 11)
    now = datetime.now(UTC)
    Store(queue.path).record(
        Event(12, now, Version(-91, 6, 5, "user", 7, "test", "changed", None, now, now, None, True))
    )
    assert queue.next_output() is None
    assert queue.stage(turn.pile, "reply", "reply", ["answer"], 11) == "failed"
    with pytest.raises(ValueError):
        queue.stage(turn.pile, "reply", "reply", ["different"], 11)


def test_retryafter_survives_restart_and_replay(queue):
    async def run():
        turn = queue.claim(4000)
        request = {"turn": turn.pile, "call_key": "a", "text": "message"}
        bot = AsyncMock()
        bot.send_rich_message.side_effect = TelegramRetryAfter(
            method=SendMessage(chat_id=-91, text="message"), message="retry", retry_after=20
        )
        out = Outputs(queue, bot, -91, 6, queue.path.parent)
        out.turn = turn.pile
        assert "pending" in await out.invoke("mimir_send", request)
        queue.finish(turn.pile, "done", "end_turn")
        queue.recover()
        new = Outputs(Turns(queue.path), bot, -91, 6, queue.path.parent)
        await new.deliver_one()
        assert bot.send_rich_message.await_count == 1
        with closing(sqlite3.connect(queue.path)) as db, db:
            db.execute("UPDATE outputs SET due=0")
        bot.send_rich_message.side_effect = None
        bot.send_rich_message.return_value = SimpleNamespace(message_id=81)
        await new.deliver_one()
        assert "sent" in await new.invoke("mimir_send", request)
        assert bot.send_rich_message.await_count == 2

    asyncio.run(run())


def test_admin_identity_and_group_routing():
    def event(chat, actor, kind):
        update = Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 5,
                    "date": 1,
                    "chat": {"id": chat, "type": kind},
                    "from": {"id": actor, "is_bot": False, "first_name": "A"},
                    "text": "/screen",
                    "message_thread_id": 6,
                },
            }
        )
        return normalize(update, -91, 6, datetime.now(UTC), frozenset({7}))

    assert event(7, 7, "private").admin == (7, "/screen")
    for e in (event(8, 8, "private"), event(-91, 7, "supergroup")):
        assert e.admin is None and e.version is None


def test_document_snapshot_replay_after_source_removed(queue):
    async def run():
        turn = queue.claim(4000)
        path = queue.path.parent / "sample.txt"
        path.write_text("original")
        bot = AsyncMock()
        bot.send_document.return_value = SimpleNamespace(
            message_id=19, document=SimpleNamespace(file_id="synthetic")
        )
        out = Outputs(queue, bot, -91, 6, path.parent)
        out.turn = turn.pile
        args = {"turn": turn.pile, "call_key": "file", "path": "sample.txt"}
        assert "sent" in await out.invoke("mimir_document", args)
        path.unlink()
        queue.finish(turn.pile, "done", "end_turn")
        out.turn = None
        assert "sent" in await out.invoke("mimir_document", args)
        bot.send_document.assert_awaited_once()

    asyncio.run(run())


def test_cancel_cannot_reach_next_turn_and_model_survives_restart(queue):
    from mimir_pm_bot.application.turns import Control
    from mimir_pm_bot.runtime.service import Worker

    async def run():
        agent = SimpleNamespace(active=True, cancel=AsyncMock(), models={"low", "high"})
        delivery = SimpleNamespace(turn=12, rate_until=0)
        worker = Worker(queue, agent, delivery, 32768, 0.3)
        await worker.panel(Control(7, "/cancel", 11))
        agent.cancel.assert_not_called()
        await worker.panel(Control(7, "/cancel", 12))
        agent.cancel.assert_awaited_once()
        await worker.panel(Control(7, "/model low", 12))
        assert Turns(queue.path).pending_model() == "low"

    asyncio.run(run())


def test_new_replay_does_not_stage_under_old_turn(queue):
    async def run():
        turn = queue.claim(4000)
        out = Outputs(queue, AsyncMock(), -91, 6, queue.path.parent)
        with pytest.raises(ValueError):
            await out.invoke(
                "mimir_send", {"turn": turn.pile, "call_key": "stale", "text": "ignored"}
            )
        assert queue.next_output() is None

    asyncio.run(run())


def test_native_rich_payloads_preserve_markdown_and_literal_panel_text():
    from mimir_pm_bot.adapters.rich_text import rich_text

    message = rich_text("**پروژه**")
    assert message.markdown == "**پروژه**" and message.is_rtl
    panel = rich_text("tool: <file> **literal**", literal=True)
    assert panel.blocks[0].text == "tool: <file> **literal**"
    assert not panel.is_rtl


def test_one_progress_card_updates_after_activity_and_recovery(queue):
    from mimir_pm_bot.adapters.acp import activity_label
    from mimir_pm_bot.adapters.turn_cards import TurnCards

    async def run():
        turn = queue.claim(4000)
        bot = AsyncMock()
        bot.send_rich_message.return_value = SimpleNamespace(message_id=83)
        cards = TurnCards(queue, bot, -91, 6)
        await cards.deliver_one()
        label = activity_label({"kind": "read", "title": "read secret path"})
        cards.activity(turn.pile, label)
        await cards.deliver_one()
        bot.edit_message_text.assert_not_awaited()
        with queue.transaction() as db:
            db.execute("UPDATE turn_cards SET due=0")
        await cards.deliver_one()
        edit = bot.edit_message_text.await_args.kwargs
        assert edit["message_id"] == 83 and label in edit["rich_message"].blocks[1].text
        assert "secret" not in edit["rich_message"].blocks[1].text
        queue.recover()
        with queue.transaction() as db:
            db.execute("UPDATE turn_cards SET due=0")
        await cards.deliver_one()
        bot.delete_message.assert_awaited_once_with(chat_id=-91, message_id=83)
        bot.send_rich_message.assert_awaited_once()
        assert bot.edit_message_text.await_count == 1

    asyncio.run(run())


def test_incident_channel_reports_failure_without_message_content(queue):
    from mimir_pm_bot.adapters.incidents import IncidentChannel

    async def run():
        t = queue.claim(4000)
        queue.stage(t.pile, "one", "send", ["private content"], None)
        output = queue.next_output()
        queue.settle(output.id, "failed", "untrusted detail")
        bot = AsyncMock()
        channel = IncidentChannel(queue, bot, -10012345)
        await channel.deliver_one()
        await channel.deliver_one()
        bot.send_rich_message.assert_awaited_once()
        dest, message = bot.send_rich_message.await_args.args
        assert dest == -10012345
        payload = message.model_dump_json()
        assert "Telegram delivery" in payload and "failed" in payload
        assert "private content" not in payload and "untrusted detail" not in payload

    asyncio.run(run())


def test_poll_disconnect_retries_the_same_checkpoint(monkeypatch):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import GetUpdates

    from mimir_pm_bot.adapters.telegram import Telegram

    async def run():
        bot = AsyncMock()
        bot.get_updates.side_effect = [
            *[TelegramNetworkError(method=GetUpdates(), message="offline") for _ in range(7)],
            [],
        ]
        source = Telegram(bot, -91, 6, 20)
        source.on_error = AsyncMock()
        monkeypatch.setattr("mimir_pm_bot.adapters.telegram.asyncio.sleep", AsyncMock())
        assert await source.fetch(44) == []
        assert [c.kwargs["offset"] for c in bot.get_updates.await_args_list] == [44] * 8
        source.on_error.assert_awaited_once()

    asyncio.run(run())
