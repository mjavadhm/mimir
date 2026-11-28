import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mimir_pm_bot.adapters.memory import work_source
from mimir_pm_bot.adapters.review_store import ReviewStore
from mimir_pm_bot.adapters.review_worker import ReviewWorker
from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.turn_store import Turns
from mimir_pm_bot.application.reviews import Review, ReviewAction
from mimir_pm_bot.domain.conversations import Event, Version
from mimir_pm_bot.profiles import servers


@pytest.fixture
def case(tmp_path):
    inbox = Store(tmp_path / "test.db")
    inbox.initialize(1, -91, 6)
    turns = Turns(inbox.path)

    def message(n):
        now = datetime.now(UTC) - timedelta(seconds=10)
        inbox.record(
            Event(
                n,
                now,
                Version(-91, 6, n, "user", 7, "team", "review PR", None, now, None, None, False),
            )
        )
        return turns.claim(32768).pile

    store = ReviewStore(turns.transaction, work_source, "abc/test", "fixed instruction")
    version = ("a" * 40, "b" * 40, "title", "body")
    gh = SimpleNamespace(
        repo="abc/test",
        pull_version=AsyncMock(return_value=version),
        checks=AsyncMock(return_value={"available": False}),
        patches=AsyncMock(return_value=([{"patch": "change"}], True)),
        post_review_comment=AsyncMock(return_value="https://example.org/receipt"),
        find_review_comment=AsyncMock(return_value="https://example.org/receipt"),
    )
    agent = SimpleNamespace(
        session="separate",
        start=AsyncMock(),
        close=AsyncMock(),
        prompt=AsyncMock(return_value="end_turn"),
        text="found issue",
        overflow=False,
    )
    factory = Mock(return_value=agent)
    worker = ReviewWorker(store, gh, tmp_path / "snapshots", factory, [], lambda pull: [])
    return store, turns, worker, message, factory


def test_version_change_delivery_and_unknown_publication(case):
    s, q, w, msg, factory = case
    req = Review(turn=msg(10), source=10, pull=7, publish=True)
    assert s.stage(req, "publish") == s.stage(req, "publish")
    assert not s.next_job()
    q.finish(req.turn, "done", "end_turn")
    asyncio.run(w.review(s.next_job()))
    directory = factory.call_args.args[0]
    assert json.loads((directory / "snapshot.json").read_text())["files"] == [{"patch": "change"}]
    assert "fixed instruction" in factory.return_value.prompt.call_args.args[0]
    factory.return_value.start.assert_awaited_once_with(mcp=[])
    w.github.pull_version.return_value = ("a" * 40, "c" * 40, "changed", "body")
    asyncio.run(w.review(s.next_job()))
    assert not s.next_action()
    asyncio.run(w.review(s.next_job()))
    asyncio.run(w.review(s.next_job()))
    row = s.next_action()
    assert row["head"] == "c" * 40 and factory.call_count == 2
    assert s.begin_post(row) == "send"
    s.recover()
    asyncio.run(w.publish(s.next_action()))
    w.github.post_review_comment.assert_not_awaited()
    assert not s.next_action()
    with q.transaction() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM outputs WHERE call_key='review/result/1'").fetchone()[
                0
            ]
            == 1
        )


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_or_publish_later(case, cancel):
    s, q, w, msg, _ = case
    turn = msg(10)
    with pytest.raises(ValueError):
        s.stage(
            Review(turn=turn, source=10, pull="https://github.com/other/repo/pull/7"), "publish"
        )
    s.stage(Review(turn=turn, source=10, pull=7), "publish")
    q.finish(turn, "done", "end_turn")
    if not cancel:
        asyncio.run(w.review(s.next_job()))
        asyncio.run(w.review(s.next_job()))
        assert not s.next_action()
    turn = msg(11)
    s.stage(ReviewAction(turn=turn, source=11, review_id=1), "cancel" if cancel else "publish")
    q.finish(turn, "done", "end_turn")
    assert not s.next_job()
    if not cancel:
        asyncio.run(w.publish(s.next_action()))
        assert "found issue" in w.github.post_review_comment.call_args.args[1]
        turn = msg(12)
        s.stage(ReviewAction(turn=turn, source=12, review_id=1), "publish")
        q.finish(turn, "done", "end_turn")
        w.github.pull_version.return_value = ("d" * 40, "c" * 40, "new", "body")
        asyncio.run(w.publish(s.next_action()))
        w.github.post_review_comment.assert_awaited_once()


def test_profiles(tmp_path):
    p = tmp_path / "mcp.toml"
    p.write_text(
        '[mimir]\nservers=["mimir"]\n[reviewer]\nservers=["docs"]\n[http.docs]\nurl="https://example.org/mcp"'
    )
    assert servers("mimir", "http://localhost/internal", path=p)[0]["name"] == "mimir"
    assert servers("reviewer", path=p)[0]["name"] == "docs"
    p.write_text('[reviewer]\nservers=["mimir"]')
    with pytest.raises(ValueError, match="management-session"):
        servers("reviewer", path=p)


def test_failed_management_turn_and_changed_context(case):
    s, q, w, msg, _ = case
    turn = msg(10)
    s.stage(Review(turn=turn, source=10, pull=7), "publish")
    q.finish(turn, "done", "cancelled")
    assert s.next_job() is None
    turn = msg(11)
    s.stage(Review(turn=turn, source=11, pull=7), "publish")
    q.finish(turn, "done", "end_turn")
    asyncio.run(w.review(s.next_job()))
    w.context = lambda pull: [{"issue": 4, "decision_current": False}]
    asyncio.run(w.review(s.next_job()))
    with q.transaction() as db:
        assert db.execute("SELECT state FROM reviews ORDER BY id").fetchall()[1][0] == "pending"
        assert not db.execute(
            "SELECT 1 FROM outputs WHERE call_key LIKE 'review/result/%'"
        ).fetchone()


def test_invalid_reviewer_output_is_bounded(case):
    s, q, w, msg, factory = case
    factory.return_value.text = ""
    turn = msg(10)
    s.stage(Review(turn=turn, source=10, pull=7), "publish")
    q.finish(turn, "done", "end_turn")
    for _ in range(4):
        row = s.next_job()
        with pytest.raises(RuntimeError, match="incomplete"):
            asyncio.run(w.review(row))
        s.retry(row, "incomplete", 0)
    assert not s.next_job()
    with q.transaction() as db:
        assert db.execute("SELECT state FROM reviews").fetchone()[0] == "failed"
        assert (
            db.execute("SELECT count(*) FROM incidents WHERE component='PR review'").fetchone()[0]
            == 1
        )


def test_review_reply_splits_between_paragraphs(case):
    s, q, _, msg, _ = case
    turn = msg(10)
    first = "a" * 1993
    second = "**finding**"
    with q.transaction() as db:
        s.reply(db, turn, 10, "result/split", first + "\n\n" + second)
        assert [row[0] for row in db.execute("SELECT payload FROM outputs ORDER BY part")] == [
            first,
            second,
        ]
