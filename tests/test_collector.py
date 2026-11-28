import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aiogram.types import Update

from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.telegram import normalize
from mimir_pm_bot.runtime.collector import collect, exclusive


def message(uid, text="hello", *, edit=False, topic=3, **fields):
    msg = dict(
        message_id=7,
        date=1710000000,
        message_thread_id=topic,
        chat={"id": -1001, "type": "supergroup"},
        text=text,
        **fields,
    )
    msg["from"] = {"id": 11, "is_bot": False, "first_name": "A"}
    if edit:
        msg["edit_date"] = 1710000002
    return normalize(
        Update.model_validate({"update_id": uid, "edited_message" if edit else "message": msg}),
        -1001,
        3,
        datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "collector.db")
    store.initialize(22, -1001, 3)
    return store


def test_ingest_and_reopen(store):
    reply = {"message_id": 5, "date": 1710000000, "chat": {"id": -1001, "type": "supergroup"}}
    events = [
        message(1, reply_to_message=reply),
        message(2, edit=True, text="changed"),
        message(3, edit=True, text="changed"),
        message(4, topic=19),
    ]
    for event in events:
        store.record(event)
    reopened = Store(store.path)
    reopened.initialize(22, -1001, 3)
    for event in events:
        assert reopened.record(event) == 5
    assert reopened.checkpoint() == 5
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("select count(*) from messages").fetchone() == (1,)
        assert db.execute("select text,is_edit from versions order by update_id").fetchall() == [
            ("hello", 0),
            ("changed", 1),
            ("changed", 1),
        ]
        assert db.execute(
            "select speaker_id,reply_to from versions where update_id=1"
        ).fetchone() == (11, 5)
        assert db.execute("select outcome from inbox where update_id=4").fetchone() == ("ignored",)


def test_first_seen_edit_from_chat(store):
    store.record(
        message(
            9,
            text=None,
            caption="document",
            edit=True,
            sender_chat={"id": -12, "type": "channel", "title": "Channel"},
        )
    )
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute(
            "select speaker_kind,speaker_id,caption,is_edit from versions"
        ).fetchall() == [("chat", -12, "document", 1)]


def test_transaction_failure_stops_batch(store):
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("""create trigger fail before insert on versions when new.update_id=2
            begin select raise(abort,'test rollback'); end""")

    class Source:
        async def fetch(self, offset):
            assert offset is None
            return [message(i) for i in (1, 2, 3)]

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(collect(Source(), store))
    assert store.checkpoint() == 2
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("select update_id from inbox").fetchall() == [(1,)]
        db.execute("drop trigger fail")
    assert store.record(message(2)) == 3


def test_second_collector(store):
    with (
        exclusive(store.path),
        pytest.raises(RuntimeError, match="Another collector"),
        exclusive(store.path),
    ):
        pytest.fail("lock unexpectedly acquired")


def test_restart_polls_durable_offset(store):
    store.record(message(44))

    class Stop(Exception):
        pass

    class Source:
        async def fetch(self, offset):
            assert offset == 45
            raise Stop

    with pytest.raises(Stop):
        asyncio.run(collect(Source(), Store(store.path)))


def test_import_boundaries():
    import ast

    root = Path(__file__).resolve().parents[1] / "src/mimir_pm_bot"
    for layer, allowed in {
        "domain": {"domain"},
        "application": {"domain", "application"},
        "runtime": {"runtime", "application", "domain"},
    }.items():
        for path in (root / layer).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [n.name for n in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for name in names:
                    assert name.split(".")[0] not in {"aiogram", "sqlite3", "sqlalchemy", "alembic"}
                    if name.startswith("mimir_pm_bot."):
                        assert name.split(".")[1] in allowed


def test_piles_restart_deadlines_and_duplicate(store):
    from dataclasses import replace
    from datetime import timedelta

    first = message(1)
    origin = first.arrived
    for seconds in range(0, 61, 3):
        event = replace(first, update_id=seconds + 1, arrived=origin + timedelta(seconds=seconds))
        Store(store.path).record(event)
        Store(store.path).record(event)
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute(
            "SELECT count(*) FROM pile_members GROUP BY pile ORDER BY pile"
        ).fetchall() == [(20,), (1,)]
        assert (
            db.execute("SELECT due FROM piles ORDER BY id LIMIT 1").fetchone()[0]
            == (origin + timedelta(seconds=60)).isoformat()
        )
