import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Update

from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.telegram import Telegram
from mimir_pm_bot.adapters.turn_store import Turns


def update(uid, chat, text, topic=None):
    msg = dict(
        message_id=uid,
        date=10,
        chat=dict(id=chat, type="private" if chat > 0 else "supergroup"),
        text=text,
        message_thread_id=topic,
    )
    msg["from"] = dict(id=chat if chat > 0 else 17, is_bot=False, first_name="A")
    return Update.model_validate(dict(update_id=uid, message=msg))


def test_change_while_old_pile_waits_and_resume(tmp_path):
    async def exercise():
        store = Store(tmp_path / "bind.db")
        store.initialize(88, -1001, 2)
        bot = AsyncMock()
        bot.get_chat.return_value = SimpleNamespace(
            id=-1002, title="Team", type="supergroup", is_forum=True
        )
        bot.send_rich_message.return_value = SimpleNamespace(
            chat=SimpleNamespace(id=-1002), message_thread_id=8
        )
        telegram = Telegram(bot, -1001, 2, 10, frozenset([7, 9]))
        await telegram.ingest(store, update(1, -1001, "old", 2))
        for uid, actor, text in [
            (2, 5, "/bind"),
            (3, 7, "/settings"),
            (4, 9, "/bind"),
            (5, 9, "/abort"),
            (6, 7, "-1002"),
        ]:
            await telegram.ingest(store, update(uid, actor, text))
        assert store.setup(5) == store.setup(9) == (False, None)
        assert store.setup(7) == (True, -1002)
        store = Store(store.path)
        await telegram.ingest(store, update(7, 7, "99"))
        assert store.destination() == (-1001, 2)
        await telegram.ingest(store, update(8, 7, "8"))
        assert store.destination() == (-1002, 8)
        assert store.checkpoint() == 9
        store.initialize(88, -1001, 2)
        assert store.destination() == (-1002, 8)
        await telegram.ingest(store, update(9, -1001, "ignored", 2))
        await telegram.ingest(store, update(10, -1002, "new", 8))
        turns = Turns(store.path)
        with turns.transaction() as db:
            assert [tuple(r) for r in db.execute("SELECT chat,topic FROM piles ORDER BY id")] == [
                (-1001, 2),
                (-1002, 8),
            ]
            assert [r[0] for r in db.execute("SELECT text FROM versions ORDER BY update_id")] == [
                "old",
                "new",
            ]
            db.execute("UPDATE piles SET due='2000-01-01T00:00:00+00:00'")
        turn = turns.claim(32768)
        turns.stage(turn.pile, "send", "send", ["from old"], None)
        out = turns.next_output()
        assert (out.chat, out.topic) == (-1001, 2)

    asyncio.run(exercise())


def test_new_install_and_interrupted_probe(tmp_path):
    async def exercise():
        s = Store(tmp_path / "setup.db")
        s.initialize(99, 0, 0)
        bot = AsyncMock()
        bot.get_chat.return_value = SimpleNamespace(
            id=-1003, title="Group", type="supergroup", is_forum=True
        )
        t = Telegram(bot, 0, 0, 5, frozenset([7]))
        await t.ingest(s, update(1, 7, "/bind"))
        await t.ingest(s, update(2, 7, "-1003"))
        assert s.reserve_probe(3)
        await t.ingest(s, update(3, 7, "4"))
        assert s.destination() == (0, 0)
        assert all(call.args[0] == 7 for call in bot.send_rich_message.await_args_list)

    asyncio.run(exercise())
