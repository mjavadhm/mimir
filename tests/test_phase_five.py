"""Acceptance checks for project reports, scheduling and local recovery."""

import asyncio
import json
import stat
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from alembic import command
from conftest import event
from sqlalchemy import select
from test_memory import begin, case_and_decision, finalize

from peyda_pm_bot.adapters.github.fake import FakeGithub
from peyda_pm_bot.adapters.github.http import HttpGithub
from peyda_pm_bot.adapters.storage.backup import copy_snapshot
from peyda_pm_bot.adapters.storage.database import Database, migration_config
from peyda_pm_bot.adapters.storage.schema import jobs, outbox, versions
from peyda_pm_bot.adapters.storage.work import SqlWork
from peyda_pm_bot.adapters.telegram.fake import FakeTransport
from peyda_pm_bot.application.actions.errors import GithubTemporary
from peyda_pm_bot.application.conversations.service import ConversationPolicy, Conversations
from peyda_pm_bot.application.work.service import WorkFollowup, WorkSchedule
from peyda_pm_bot.domain.actions.models import ActionSpec, RemoteIssue
from peyda_pm_bot.domain.memory.models import MemoryRecord, MemoryView
from peyda_pm_bot.domain.work.models import BranchProtection, PullSummary, WorkScan, WorkView
from peyda_pm_bot.domain.work.report import reminders, report
from peyda_pm_bot.runtime.delivery import Sender

TEHRAN = ZoneInfo("Asia/Tehran")
FIRST_DAY = datetime(2030, 1, 8, 11, tzinfo=TEHRAN)
REPOSITORY = "https://github.com/example/project"


def issue(number, status="blocked", owner=("alice",)):
    return RemoteIssue(
        number,
        f"کار {number}",
        "private body",
        "closed" if status == "done" else "open",
        (f"pm:{status}",),
        owner,
        "2030-01-01T00:00:00Z",
        f"{REPOSITORY}/issues/{number}",
    )


def followup(database, github=None, *, clock=FIRST_DAY.timestamp, scheduled=False):
    labels = FakeGithub().status_labels
    schedule = WorkSchedule(TEHRAN, time(9) if scheduled else None, time(10) if scheduled else None)
    return WorkFollowup(database.unit, labels, (-1001, 42), schedule, github, clock)


async def deliveries(database):
    async with database.unit() as unit:
        return (await unit.connection.execute(select(outbox).order_by(outbox.c.id))).all()


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"speaker_id": 8}, True),
        ({"speaker_id": 9}, False),
        ({"sender_chat_id": -1001}, False),
        ({"is_edit": True}, False),
        ({"topic_id": 43}, False),
        ({"chat_id": -1002}, False),
    ],
)
async def test_report_is_local_and_respects_topic_and_members(database, changes, expected):
    scheduler = followup(database)
    async with database.unit() as unit:
        await unit.work.observe(issue(1, owner=()))
    policy = ConversationPolicy(-1001, 42, frozenset({7}), frozenset({8}))
    conversations = Conversations(database.unit, policy, queue_enabled=True, followup=scheduler)
    update = event(text="/report", **changes)
    await conversations.ingest([update])
    await conversations.process_one()
    await conversations.ingest([update])
    assert not await conversations.process_one()

    transport = FakeTransport()
    assert await Sender(database.unit, transport).send_one() is expected
    if expected:
        [result] = transport.sent
        assert (result.chat_id, result.topic_id, result.reply_to_id) == (-1001, 42, 100)
        assert "مسدود" in result.text and f"{REPOSITORY}/issues/1" in result.text
        assert "هنوز مشاهده نشده" in result.text and "private body" not in result.text
    async with database.unit() as unit:
        assert (await unit.connection.execute(select(jobs))).first() is None


def test_report_distinguishes_open_questions_and_commitments_from_decisions():
    def note(category, title, status="active"):
        return MemoryView(MemoryRecord("note", title, category=category, status=status))

    result = report(
        [],
        [
            note("question", "ابهام معتبر"),
            note("commitment", "تعهد عضو"),
            note("decision", "تصمیم قطعی"),
            note("question", "سؤال بسته", "inactive"),
        ],
        WorkScan(),
        FIRST_DAY,
        TEHRAN,
        timedelta(minutes=30),
    )
    assert "ابهام معتبر" in result.text and "تعهد عضو" in result.text
    assert "تصمیم قطعی" not in result.text and "سؤال بسته" not in result.text


def test_reminders_use_recorded_due_dates_dependencies_and_activity():
    due = (FIRST_DAY - timedelta(days=1)).isoformat()
    views = [
        WorkView(issue(1, "in_progress"), "in_progress", due_at=due, dependencies=(2, 3, 99)),
        WorkView(issue(2, "ready"), "ready"),
        WorkView(issue(3, "done"), "done", due_at=due),
    ]
    [notice] = reminders(views, FIRST_DAY, TEHRAN, timedelta(hours=72))
    assert notice.issue_number == 1
    assert "گذشتن از موعد" in notice.text
    assert "وابستگی باز #2" in notice.text and "#3" not in notice.text and "#99" not in notice.text
    assert "بدون فعالیت تازه" in notice.text


async def test_refresh_preserves_metadata_and_last_good_state_on_partial_and_rate_limit(database):
    now = FIRST_DAY

    class Feed(FakeGithub):
        retry_after = None
        reads_count = 0

        async def issues(self, query=""):
            # Opening a short transaction here also proves the provider is outside the UoW.
            async with database.unit():
                pass
            self.reads_count += 1
            if self.retry_after:
                raise GithubTemporary(self.retry_after)
            return await super().issues(query)

    github = Feed()
    github.issue_data.update({1: issue(1), 2: issue(2, "ready")})
    github.pull_data[3] = PullSummary(
        3, "کار آماده برای بررسی", f"{REPOSITORY}/pull/3", "open", False, ""
    )
    github.protection = BranchProtection(True, True, None)
    async with database.unit() as unit:
        await unit.work.observe(
            issue(1),
            ActionSpec(
                "update_issue", (10,), issue_number=1, case_ids=("case",), dependencies=(2,)
            ),
        )

    scheduler = followup(database, github, clock=lambda: now.timestamp())
    await scheduler.run_once()
    await scheduler.run_once()
    assert github.reads_count == 1
    async with database.unit() as unit:
        scan = await unit.work.scan()
        assert scan.complete and scan.issue_numbers == (1, 2)
        assert scan.protection.pull_requests_required and scan.pulls[0].number == 3
        assert "وابستگی #2" in await scheduler.report_text(unit)
        assert (await unit.work.list(github.status_labels))[0].case_ids == ("case",)

    now += timedelta(minutes=16)
    github.complete = False
    github.issue_data = {1: issue(1, "in_progress")}
    github.pull_data.clear()
    await scheduler.run_once()
    async with database.unit() as unit:
        scan = await unit.work.scan()
        assert not scan.complete and scan.issue_numbers == (1, 2)
        assert scan.pulls[0].number == 3

    now += timedelta(minutes=31)
    github.retry_after = 500
    await scheduler.run_once()
    async with database.unit() as unit:
        scan = await unit.work.scan()
        assert scan.next_attempt_at == now.timestamp() + 500
        assert "ناقص، قدیمی" in await scheduler.report_text(unit)
        assert (await unit.work.list(github.status_labels))[0].status == "in_progress"
    now += timedelta(seconds=499)
    await scheduler.run_once()
    assert github.reads_count == 3
    assert github.mutations == 0


async def test_schedule_is_atomic_and_catches_up_once_after_wake(tmp_path, monkeypatch):
    path = tmp_path / "scheduler.db"
    now = FIRST_DAY
    github = FakeGithub()
    github.issue_data[1] = issue(1)
    database = Database(path)
    await database.initialize(path, "test")
    try:
        scheduler = followup(database, github, clock=lambda: now.timestamp(), scheduled=True)
        with monkeypatch.context() as patch:
            original_save = SqlWork.save_schedule

            async def fail_last_slot(self, name, state):
                if name == "reminder":
                    raise RuntimeError("crash before commit")
                await original_save(self, name, state)

            patch.setattr(SqlWork, "save_schedule", fail_last_slot)
            with pytest.raises(RuntimeError, match="crash"):
                await scheduler.run_once()
        assert not await deliveries(database)

        await asyncio.gather(scheduler.run_once(), scheduler.run_once())
        assert len(await deliveries(database)) == 2
        # Lose the response after a real sender claim. Startup must not send that row again.
        async with database.unit() as unit:
            assert await unit.outgoing.claim_delivery(now) is not None
    finally:
        await database.close()

    restored = Database(path)
    try:
        await restored.initialize(path, "test")
        now += timedelta(days=4)
        scheduler = followup(restored, github, clock=lambda: now.timestamp(), scheduled=True)
        github.complete = False
        await scheduler.run_once()
        assert len(await deliveries(restored)) == 2
        github.complete = True
        now += timedelta(minutes=16)
        await scheduler.run_once()
        assert len(await deliveries(restored)) == 2
        github.issue_data[1] = replace(github.issue_data[1], title="مانع جدید", assignees=("bob",))
        now += timedelta(days=4)
        await scheduler.run_once()
        rows = await deliveries(restored)
        assert len(rows) == 4
        assert rows[0].status == "unknown"
        assert all(row.topic_id == 42 for row in rows)
        assert rows[2].delivery_key.endswith(now.date().isoformat())
        assert rows[3].delivery_key.endswith(now.date().isoformat())
        assert "bob" in rows[3].text

        now += timedelta(days=1)
        github.issue_data[1] = issue(1, "done", owner=("bob",))
        await scheduler.run_once()
        assert len(await deliveries(restored)) == 5  # Changed report, no reminder.
        now += timedelta(days=1)
        github.issue_data[1] = issue(1, owner=("bob",))
        await scheduler.run_once()
        assert len(await deliveries(restored)) == 7  # Same blocker may recur after closure.
    finally:
        await restored.close()


async def test_snapshot_contains_wal_evidence_and_rejects_existing_or_wrong_project(
    database, conversations, tmp_path
):
    request, token, memory = await begin(database, conversations)
    _, decision = await case_and_decision(memory, token)
    await finalize(database, request)

    original = event(20, message_id=200, text="متن اصلی")
    edit = event(21, message_id=200, text="متن ویرایش‌شده", is_edit=True)
    for message in (original, edit):
        await conversations.ingest([message])
        await conversations.process_one()

    source = Path(database.engine.url.database)
    assert source.with_suffix(".db-wal").stat().st_size > 0
    backup = tmp_path / "backup.db"
    restored_path = tmp_path / "restored.db"
    snapshot = await asyncio.to_thread(copy_snapshot, source, backup, "test")
    assert snapshot.revision == "0006"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        await asyncio.to_thread(copy_snapshot, source, backup)
    with pytest.raises(ValueError, match="another project"):
        await asyncio.to_thread(copy_snapshot, backup, restored_path, "different")
    assert not restored_path.exists()

    await asyncio.to_thread(copy_snapshot, backup, restored_path, "test")
    restored = Database(restored_path)
    try:
        await restored.initialize(restored_path, "test")
        async with restored.unit() as unit:
            rows = (
                await unit.connection.execute(select(versions).order_by(versions.c.update_id))
            ).all()
            assert [json.loads(row.payload)["text"] for row in rows[-2:]] == [
                "متن اصلی",
                "متن ویرایش‌شده",
            ]
            assert (await unit.memory.history(decision.record.id))[0].record.source_update_ids
            assert (await unit.connection.execute(select(outbox.c.status))).scalar() == "pending"
    finally:
        await restored.close()


async def test_upgrade_from_0005_keeps_work_data(database):
    path = Path(database.engine.url.database)
    async with database.unit() as unit:
        await unit.work.observe(issue(1))
    await database.close()
    await asyncio.to_thread(command.downgrade, migration_config(path), "0005")
    await database.initialize(path, "test")
    async with database.unit() as unit:
        assert await unit.work.scan() == WorkScan()
        assert (await unit.work.list(FakeGithub().status_labels))[0].issue.number == 1


async def test_github_follows_pull_and_branch_metadata_without_writes():
    async def handler(request):
        assert request.method == "GET"
        suffix = request.url.path.removeprefix("/repos/example/project/")
        values = {
            "pulls": [
                {
                    "number": 1,
                    "title": "Review request",
                    "html_url": f"{REPOSITORY}/pull/1",
                    "state": "closed",
                    "merged_at": "2030-01-08T00:00:00Z",
                    "updated_at": "2030-01-08T00:00:00Z",
                    "draft": False,
                }
            ],
            "branches/main": {"protected": True},
            "rules/branches/main": [{"type": "pull_request"}],
        }
        if suffix == "branches/main/protection":
            return httpx.Response(403, json={"message": "No administration access"})
        return httpx.Response(200, json=values[suffix])

    github = HttpGithub(
        "example/project", "test-only", "bot", {}, transport=httpx.MockTransport(handler)
    )
    try:
        pulls, complete = await github.pull_summaries()
        assert complete and pulls[0].state == "merged"
        assert await github.branch_protection() == BranchProtection(True, True, None)
    finally:
        await github.close()
