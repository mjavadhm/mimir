import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mimir_pm_bot.adapters.memory import run
from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.turn_store import Turns
from mimir_pm_bot.application.memory import Read, Search, Sources, Stage
from mimir_pm_bot.domain.conversations import Event, Version


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "memory.db"
    store = Store(path)
    store.initialize(10, -100123, 2)
    queue = Turns(path)

    def message(uid, mid, text, edit=False):
        stamp = datetime.now(UTC) - timedelta(seconds=5)
        store.record(
            Event(
                uid,
                stamp,
                Version(
                    -100123,
                    2,
                    mid,
                    "user",
                    7,
                    "test",
                    text,
                    None,
                    stamp,
                    stamp if edit else None,
                    None,
                    edit,
                ),
            )
        )

    message(11, 1, "اصل")
    turn = queue.claim(32768).pile
    return store, queue, message, turn


def request(turn, **overrides):
    values = dict(
        turn=turn,
        operation="one",
        subject="پيام‌ خوش‌آمد",
        expected_revision=0,
        text="ارسال پس از ثبت",
        sources=[11],
    )
    return Stage.model_validate(values | overrides)


def call(queue, req):
    with queue.transaction() as db:
        return run(db, req)


def test_record_and_revise_after_restart(setup):
    store, q, message, t = setup
    req = request(t)
    created = call(q, req)
    duplicate = call(q, req)
    assert duplicate["decision"] == created["decision"]
    ident = created["decision"]
    assert call(q, Search(turn=t, query="پیام خوش آمد"))[0]["decision"] == ident
    assert call(q, Read(turn=t, decision=ident))[0]["committed_at"] is None
    assert q.stage(t, "publish", "reply", ["ثبت شد"], 11) == "held"
    assert q.next_output() is None
    q.finish(t, "done", "end_turn")
    assert q.next_output().payload == "ثبت شد"
    message(12, 2, "اصلاح")
    t = q.claim(32768).pile
    with pytest.raises(ValueError):
        call(q, request(t, decision=ident, expected_revision=3))
    call(
        q, request(t, decision=ident, expected_revision=1, text="جدید", reason="دلیل", sources=[12])
    )
    q.finish(t, "done", "end_turn")
    message(13, 1, "ویرایش منبع", True)
    q = Turns(store.path)
    t = q.claim(32768).pile
    rows = call(q, Read(turn=t, decision=ident, history=True))
    assert [r["revision"] for r in rows] == [1, 2]
    assert rows[0]["reason"] is None
    assert rows[0]["sources"][0]["edited_since"]
    assert rows[1]["sources"][0]["link"] == "https://t.me/c/123/2/2"
    assert [r["text"] for r in call(q, Sources(turn=t, versions=[11]))] == ["اصل", "ویرایش منبع"]
    call(
        q,
        request(
            t,
            decision=ident,
            expected_revision=2,
            status="inactive",
            reason="جایگزینی",
            sources=[13],
        ),
    )
    assert call(q, Search(turn=t, query="پیام")) == []


@pytest.mark.parametrize("outcome", ["cancelled", "unknown", "restart", "edit"])
def test_cancel_or_stale_discards_memory_and_announcement(setup, outcome):
    _, q, message, t = setup
    with pytest.raises(ValueError):
        call(q, request(t, sources=[999]))
    call(q, request(t))
    with pytest.raises(ValueError):
        call(q, request(t, text="different"))
    q.stage(t, "announce", "send", ["announcement"], None)
    if outcome == "edit":
        message(12, 1, "edited", True)
    if outcome == "restart":
        q.recover()
    else:
        q.finish(
            t,
            "unknown" if outcome == "unknown" else "done",
            "end_turn" if outcome == "edit" else outcome,
        )
    with q.transaction() as db:
        assert db.execute("SELECT count(*) FROM decision_versions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM subjects").fetchone()[0] == 0
    assert q.next_output() is None


def test_finish_rolls_back_every_write(setup):
    _, q, _, t = setup
    call(q, request(t))
    q.stage(t, "announce", "send", ["announcement"], None)
    with q.transaction() as db:
        db.execute("""CREATE TRIGGER fail_finish BEFORE UPDATE OF state ON piles
                    BEGIN SELECT RAISE(ABORT,'fault'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        q.finish(t, "done", "end_turn")
    with q.transaction() as db:
        assert db.execute("SELECT committed_at FROM decision_versions").fetchone()[0] is None
        assert db.execute("SELECT state FROM outputs").fetchone()[0] == "held"
        assert db.execute("SELECT state FROM piles").fetchone()[0] == "active"


def test_subject_opening_and_multiple_decisions(setup):
    _, q, message, t = setup
    opening = request(t, entity="subject", text=None)
    created = call(q, opening)
    assert call(q, opening) == created
    sid = created["subject_id"]
    assert call(q, Read(turn=t, subject_id=sid))["decisions"] == []
    assert call(q, Search(turn=t, query="پيام"))[0]["subject_id"] == sid
    assert q.stage(t, "notice", "send", ["opened"], None) == "held"
    q.finish(t, "done", "end_turn")
    message(12, 2, "new")
    t = q.claim(32768).pile
    for op in ("d1", "d2"):
        call(q, request(t, operation=op, text=op, sources=[12]))
    assert {r["text"] for r in call(q, Read(turn=t, subject_id=sid))} == {"d1", "d2"}
    q.finish(t, "done", "cancelled")
    message(13, 3, "another turn")
    t = q.claim(32768).pile
    assert call(q, Read(turn=t, subject_id=sid))["decisions"] == []
