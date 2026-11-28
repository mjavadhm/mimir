import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from mimir_pm_bot.adapters.github import GitHubError
from mimir_pm_bot.adapters.memory import committed_decision, run, work_source
from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.turn_store import Turns
from mimir_pm_bot.adapters.work import Work
from mimir_pm_bot.application.memory import Stage
from mimir_pm_bot.application.work import Prepare, Submit
from mimir_pm_bot.domain.conversations import Event, Version


class Remote:
    repo = "test/repo"
    calls = 0
    failure = None

    async def create(self, title, body):
        self.calls += 1
        if self.failure:
            raise GitHubError(self.failure, "simulated")
        assert "https://t.me/c/123/2/11" in body
        return 42

    async def reconcile(self, marker):
        return 42

    async def metadata(self, number, pull=False):
        return {"number": number, "assignees": [], "state": "merged" if pull else "open"}


@pytest.fixture
def case(tmp_path):
    store = Store(tmp_path / "work.db")
    store.initialize(1, -100123, 2)
    q = Turns(store.path)
    remote = Remote()
    w = Work(q.transaction, remote, committed_decision, work_source)
    w.bind()

    def message(uid, mid=None):
        stamp = datetime.now(UTC) - timedelta(seconds=8)
        store.record(
            Event(
                uid,
                stamp,
                Version(
                    -100123,
                    2,
                    mid or uid,
                    "user",
                    uid,
                    str(uid),
                    "test https://github.com/test/repo/pull/7",
                    None,
                    stamp,
                    stamp if mid else None,
                    None,
                    bool(mid),
                ),
            )
        )

    message(11)
    t = q.claim(32768).pile
    with q.transaction() as db:
        d = run(
            db,
            Stage(
                turn=t,
                operation="memory",
                subject="subject",
                text="Orange",
                expected_revision=0,
                sources=[11],
            ),
        )["decision"]
    q.finish(t, "done", "end_turn")
    message(12)
    t = q.claim(32768).pile
    args = dict(
        decision=d, decision_revision=1, expected_revision=0, title="Test", description="Demo"
    )
    draft = w.local(Prepare(turn=t, **args))
    q.finish(t, "done", "end_turn")
    message(13)
    t = q.claim(32768).pile
    return w, q, remote, message, draft, t, args


def test_revision_cancel_and_expired_evidence(case):
    w, q, remote, message, d, t, args = case
    new = w.local(Prepare.model_validate(args | dict(turn=t, expected_revision=1, title="New")))
    assert new["revision"] == 2
    with pytest.raises(ValueError):
        w.local(Submit(turn=t, draft=d["draft"], revision=1, source=13))
    w.local(Submit(turn=t, draft=d["draft"], revision=2, source=13))
    q.finish(t, "done", "cancelled")
    assert not asyncio.run(w.deliver_one())
    message(14)
    t = q.claim(32768).pile
    w.local(Submit(turn=t, draft=d["draft"], revision=1, source=14))
    q.finish(t, "done", "end_turn")
    message(15, 11)
    assert not asyncio.run(w.deliver_one())
    assert remote.calls == 0


@pytest.mark.parametrize("failure", [None, "unknown", "pending", "failed"])
def test_replay_receipt_and_delivery(case, failure):
    w, q, remote, message, d, t, _ = case
    req = Submit(turn=t, draft=d["draft"], revision=1, source=13)
    assert w.local(req) == w.local(req)
    assert not asyncio.run(w.deliver_one())
    q.finish(t, "done", "end_turn")
    remote.failure = failure
    assert asyncio.run(w.deliver_one())
    q.recover()
    if failure == "pending":
        assert not asyncio.run(w.deliver_one())
    if failure == "unknown":
        with q.transaction() as db:
            db.execute("UPDATE issue_actions SET due=0")
        assert asyncio.run(w.deliver_one())
    assert remote.calls == 1
    if failure in {"failed", "pending"}:
        with q.transaction() as db:
            assert db.execute("SELECT state FROM issue_actions").fetchone()[0] == failure
        return
    assert not asyncio.run(w.deliver_one())
    with q.transaction() as db:
        assert db.execute("SELECT state,number FROM issue_actions").fetchone()[:] == ("sent", 42)
    for uid in (14, 15):
        message(uid)
        turn = q.claim(32768).pile
        asyncio.run(w.invoke("start_work", dict(turn=turn, issue=42, source=uid)))
        q.finish(turn, "done", "end_turn")
    message(16)
    turn = q.claim(32768).pile
    asyncio.run(w.invoke("link_pull", dict(turn=turn, issue=42, source=16, pull=7)))
    status = json.loads(asyncio.run(w.invoke("work_status", dict(turn=turn, issue=42))))
    assert [e["speaker_id"] for e in status["events"] if not e["pull"]] == [14, 15]
    assert status["github"]["pulls"][0]["state"] == "merged"
    assert status["github"]["issue"]["assignees"] == []


def test_changed_decision_and_changed_repository(case):
    w, q, remote, _, d, t, args = case
    with q.transaction() as db:
        run(
            db,
            Stage(
                turn=t,
                operation="change",
                decision=args["decision"],
                subject="subject",
                text="Green",
                expected_revision=1,
                sources=[13],
            ),
        )
    w.local(Submit(turn=t, draft=d["draft"], revision=1, source=13))
    q.finish(t, "done", "end_turn")
    assert not asyncio.run(w.deliver_one())
    assert remote.calls == 0
    remote.repo = "wrong/target"
    with pytest.raises(ValueError, match="another GitHub repository"):
        w.bind()


def test_crash_during_post_only_reconciles(case):
    w, q, remote, _, d, t, _ = case
    w.local(Submit(turn=t, draft=d["draft"], revision=1, source=13))
    q.finish(t, "done", "end_turn")
    assert w.claim()["state"] == "pending"
    q.recover()
    assert asyncio.run(w.deliver_one())
    assert remote.calls == 0
    assert q.next_output().payload == "https://github.com/test/repo/issues/42"


def test_github_reconciliation_needs_unique_author_marker_and_complete_scan():
    from mimir_pm_bot.adapters.github import GitHub

    class Api(GitHub):
        async def request(self, path, data=None):
            return self.rows

    api = Api("test/repo", "fake", "actor")
    item = dict(
        number=3,
        html_url="https://github.com/test/repo/issues/3",
        body="<!-- op -->",
        user=dict(login="actor"),
    )
    for rows, number in [
        ([item], 3),
        ([item, item], None),
        ([item | dict(pull_request={})], None),
        ([item | dict(user=dict(login="other"))], None),
        ([item] * 100, None),
    ]:
        api.rows = rows
        assert asyncio.run(api.reconcile("<!-- op -->")) == number


@pytest.mark.parametrize(
    ("status", "headers", "state"),
    [(403, {}, "failed"), (429, {"Retry-After": "120"}, "pending"), (503, {}, "unknown")],
)
def test_http_write_classification(monkeypatch, status, headers, state):
    from unittest.mock import AsyncMock, MagicMock

    from mimir_pm_bot.adapters.github import GitHub

    response = MagicMock(status=status, headers=headers)
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    client = MagicMock()
    client.request.return_value = response_context
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    monkeypatch.setattr("mimir_pm_bot.adapters.github.aiohttp.ClientSession", lambda **kw: context)
    with pytest.raises(GitHubError) as caught:
        asyncio.run(GitHub("test/repo", "fixture", "actor").create("Draft", "Body"))
    assert caught.value.state == state
    if status == 429:
        import time

        assert caught.value.due >= time.time() + 119
