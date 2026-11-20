"""Actions become externally runnable only after a valid successful turn."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import func, select
from test_memory import begin

from peyda_pm_bot.adapters.github.fake import FakeGithub
from peyda_pm_bot.adapters.storage.schema import actions, outbox
from peyda_pm_bot.application.actions.errors import GithubPermanent
from peyda_pm_bot.application.actions.executor import ActionExecutor
from peyda_pm_bot.application.actions.service import ActionService
from peyda_pm_bot.domain.actions.models import ActionSpec, RemoteComment, RemoteIssue
from peyda_pm_bot.domain.agent.models import AgentResult
from peyda_pm_bot.domain.memory.models import MemoryRecord


async def prepare(database, conversations, spec=None, github=None):
    request, token, memory = await begin(database, conversations)
    github = github or FakeGithub()
    service = ActionService(database.unit, github, "test", github.status_labels)
    spec = spec or ActionSpec(
        "create_issue", (10,), title="Login", body="Problem\nScope\nAcceptance criteria"
    )
    draft = await service.prepare(token, "prepare", spec)
    return request, token, memory, github, service, draft


async def publish(database, request):
    async with database.unit() as unit:
        assert await unit.queue.wanted(request)
        await unit.memory.commit(request)
        await unit.actions.publish(request)
        await unit.queue.complete(request, AgentResult("silent"))


async def test_draft_submission_and_execution(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    assert github.mutations == 0
    assert await service.prepare(token, "prepare", draft.spec) == draft
    with pytest.raises(ValueError, match="Stale"):
        await service.submit(token, draft.id, 999)
    await service.submit(token, draft.id, draft.revision)
    assert not await ActionExecutor(database.unit, github).run_one()
    await publish(database, request)
    executor = ActionExecutor(database.unit, github)
    assert await executor.run_one()
    assert not await executor.run_one()
    assert github.mutations == 1
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "succeeded"
        assert (
            await unit.connection.scalar(
                select(func.count()).select_from(outbox).where(outbox.c.purpose == "action")
            )
            == 1
        )


async def test_unsubmitted_draft_and_cancelled_turn(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await publish(database, request)
    assert not await ActionExecutor(database.unit, github).run_one()
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "draft"
    with pytest.raises(ValueError):
        await service.submit(token, draft.id, draft.revision)


async def test_lost_reply_reconciles_without_second_create(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    github.lose_next_reply = True
    executor = ActionExecutor(database.unit, github)
    await executor.run_one()
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "unknown"
        await unit.actions.retry(draft.id)
    await executor.run_one()
    assert github.mutations == 1 and len(github.issue_data) == 1
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "succeeded"


async def test_incomplete_reconciliation_never_resends(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    github.lose_next_reply = True
    executor = ActionExecutor(database.unit, github)
    await executor.run_one()
    github.complete = False
    async with database.unit() as unit:
        await unit.actions.retry(draft.id)
    await executor.run_one()
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "unknown"
    assert github.mutations == 1


async def test_cancel_before_send(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    async with database.unit() as unit:
        assert await unit.actions.cancel(draft.id) == "cancelled"
    assert not await ActionExecutor(database.unit, github).run_one()
    assert github.mutations == 0


async def test_source_edit_makes_action_stale(database, conversations):
    from conftest import event
    from test_phase_two import batch

    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    await batch(database, conversations, [event(11, text="do not create", is_edit=True)], 110)
    assert not await ActionExecutor(database.unit, github).run_one()
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "stale"


async def test_remote_change_is_not_overwritten(database, conversations):
    github = FakeGithub()
    github.issue_data[1] = RemoteIssue(
        1, "Old", "body", "open", ("team",), (), "t1", "https://github.com/example/project/issues/1"
    )
    spec = ActionSpec("update_issue", (10,), issue_number=1, title="Requested")
    request, token, memory, github, service, draft = await prepare(
        database, conversations, spec, github
    )
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    github.issue_data[1] = replace(github.issue_data[1], title="Human edit", updated_at="t2")
    await ActionExecutor(database.unit, github).run_one()
    assert github.mutations == 0
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "stale"


async def test_update_preserves_unrelated_labels_and_maps_done(database, conversations):
    github = FakeGithub()
    github.issue_data[1] = RemoteIssue(
        1,
        "Old",
        "body",
        "open",
        ("team", "pm:ready"),
        (),
        "t1",
        "https://github.com/example/project/issues/1",
    )
    spec = ActionSpec(
        "update_issue", (10,), issue_number=1, work_status="done", assignees=("alice",)
    )
    request, token, memory, github, service, draft = await prepare(
        database, conversations, spec, github
    )
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    await ActionExecutor(database.unit, github).run_one()
    assert set(github.issue_data[1].labels) == {"team", "pm:done"}
    assert github.issue_data[1].state == "closed"


async def test_pr_write_and_other_authors_comment_denied(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    github.issue_data[1] = RemoteIssue(1, "PR", "body", "open", (), (), "t", "url", True)
    with pytest.raises(ValueError, match="read-only"):
        await service.prepare(
            token, "pr", ActionSpec("update_issue", (10,), issue_number=1, title="No")
        )
    github.issue_data[1] = replace(github.issue_data[1], is_pull_request=False)
    github.comment_data[5] = RemoteComment(5, 1, "human", "alice", "t", "url")
    with pytest.raises(ValueError, match="configured bot"):
        await service.prepare(
            token,
            "comment",
            ActionSpec("edit_comment", (10,), issue_number=1, comment_id=5, body="No"),
        )


async def test_case_link_projection_and_memory_read(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    case = await memory.prepare(token, "case", MemoryRecord("case", "Login"))
    changed = replace(
        draft.spec,
        case_ids=(case.record.id,),
        dependencies=(8,),
        due_at="2026-12-01T00:00:00+00:00",
    )
    draft = await service.prepare(token, "edit", changed, draft.id, draft.revision)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    await ActionExecutor(database.unit, github).run_one()
    async with database.unit() as unit:
        links = await unit.work.links(case.record.id)
        assert links[0].issue_number == 1
        view = (await unit.work.list(github.status_labels))[0]
        assert view.dependencies == (8,) and view.due_at


async def test_executor_claims_serially(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    executor = ActionExecutor(database.unit, github)
    assert sorted(await asyncio.gather(executor.run_one(), executor.run_one())) == [False, True]
    assert github.mutations == 1


async def test_publication_rollback_with_invalid_agent_output(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    with pytest.raises(ValueError):
        async with database.unit() as unit:
            await unit.memory.commit(request)
            await unit.actions.publish(request)
            await unit.queue.complete(request, AgentResult("reply", "bad target", 999))
    async with database.unit() as unit:
        assert not (await unit.connection.execute(select(actions))).first()
    assert github.mutations == 0


async def test_comment_lost_reply_and_edit_owned_comment(database, conversations):
    github = FakeGithub()
    github.issue_data[1] = RemoteIssue(1, "Issue", "body", "open", (), (), "t", "url")
    spec = ActionSpec("add_comment", (10,), issue_number=1, body="Discussion")
    request, token, memory, github, service, draft = await prepare(
        database, conversations, spec, github
    )
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    github.lose_next_reply = True
    executor = ActionExecutor(database.unit, github)
    await executor.run_one()
    async with database.unit() as unit:
        await unit.actions.retry(draft.id)
    await executor.run_one()
    assert github.mutations == 1 and len(github.comment_data) == 1
    from test_memory import begin

    request, token, memory = await begin(database, conversations, 11)
    edited = await service.prepare(
        token,
        "edit-comment",
        ActionSpec("edit_comment", (11,), issue_number=1, comment_id=1, body="Updated"),
    )
    await service.submit(token, edited.id, edited.revision)
    await publish(database, request)
    await executor.run_one()
    assert github.comment_data[1].body.startswith("Updated")


async def test_result_commit_failure_recovers_without_resend(database, conversations, monkeypatch):
    from peyda_pm_bot.adapters.storage.store import SqlStore

    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)

    async def fail_notice(*args):
        raise RuntimeError("disk failure after GitHub reply")

    with monkeypatch.context() as patch:
        patch.setattr(SqlStore, "action_notice", fail_notice)
        with pytest.raises(RuntimeError):
            await ActionExecutor(database.unit, github).run_one()
    assert github.mutations == 1
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "executing"
    await ActionExecutor(database.unit, github).run_one()
    assert github.mutations == 1
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "succeeded"


async def test_attempt_history_survives_retry_and_unknown_budget(database, conversations):
    from peyda_pm_bot.adapters.storage.schema import action_attempts

    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    github.errors.append(GithubPermanent("configuration problem"))
    executor = ActionExecutor(database.unit, github)
    await executor.run_one()
    async with database.unit() as unit:
        await unit.actions.retry(draft.id)
    await executor.run_one()
    async with database.unit() as unit:
        rows = (
            (
                await unit.connection.execute(
                    select(action_attempts).order_by(action_attempts.c.attempt)
                )
            )
            .mappings()
            .all()
        )
        assert [row["status"] for row in rows] == ["failed", "succeeded"]
        assert [row["attempt"] for row in rows] == [1, 2]


async def test_new_draft_revision_does_not_reuse_abandoned_revision(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await publish(database, request)
    from test_memory import begin

    request2, token2, _ = await begin(database, conversations, 11)
    abandoned = await service.prepare(
        token2,
        "edit",
        replace(draft.spec, title="Abandoned", source_update_ids=(11,)),
        draft.id,
        draft.revision,
    )
    async with database.unit() as unit:
        await unit.queue.fail(request2, "failed")
    request3, token3, _ = await begin(database, conversations, 12)
    valid = await service.prepare(
        token3,
        "edit",
        replace(draft.spec, title="Valid", source_update_ids=(12,)),
        draft.id,
        draft.revision,
    )
    assert valid.revision > abandoned.revision
    await service.submit(token3, valid.id, valid.revision)
    await publish(database, request3)
    await ActionExecutor(database.unit, github).run_one()
    assert github.issue_data[1].title == "Valid"


async def test_malformed_control_does_not_poison_inbox(database, conversations):
    from conftest import event
    from test_phase_two import batch

    await batch(database, conversations, [event(text="/cancel_action missing")])
    async with database.unit() as unit:
        assert await unit.incoming.next_input() is None
        assert "نامعتبر" in await unit.connection.scalar(select(outbox.c.text))


async def test_loopback_host_lifecycle_is_wired_into_application(tmp_path):
    from peyda_pm_bot.adapters.telegram.fake import FakeTransport
    from peyda_pm_bot.bootstrap import application
    from peyda_pm_bot.config import Settings

    settings = Settings(
        project_id="project",
        group_id=-1001,
        topic_id=42,
        admin_ids={7},
        database_path=tmp_path / "app.db",
    )
    transport = FakeTransport()
    provider = FakeGithub()
    async with application(settings, transport, github=provider) as app:
        assert app.github and app.executor
    assert provider.closed and transport.closed


async def test_unknown_budget_pauses_without_resend(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    github.lose_next_reply = True
    executor = ActionExecutor(database.unit, github)
    await executor.run_one()
    github.issue_data.clear()  # accepted write is no longer discoverable; do not infer nonexecution
    for _attempt in range(4):
        async with database.unit() as unit:
            await unit.connection.execute(
                actions.update().where(actions.c.id == draft.id).values(due=0)
            )
        await executor.run_one()
    assert not await executor.run_one()
    assert github.mutations == 1
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "unknown"
        assert await unit.connection.scalar(select(actions.c.due)) > 250000000000


async def test_edit_does_not_erase_local_due_or_dependencies(database, conversations):
    github = FakeGithub()
    github.issue_data[1] = RemoteIssue(1, "Old", "body", "open", ("team",), (), "t1", "url")
    original = ActionSpec(
        "update_issue",
        (10,),
        issue_number=1,
        due_at="2026-12-01T00:00:00+00:00",
        dependencies=(2,),
        priority="high",
    )
    async with database.unit() as unit:
        await unit.work.observe(github.issue_data[1], original)
        await unit.work.observe(
            replace(github.issue_data[1], title="New", updated_at="t2"),
            ActionSpec("update_issue", (10,), issue_number=1, title="New"),
        )
        view = (await unit.work.list(github.status_labels))[0]
        assert view.dependencies == (2,) and view.priority == "high" and view.due_at


async def test_parallel_code_reads_pin_same_commit(database, conversations):
    import json

    request, token, memory, github, service, draft = await prepare(database, conversations)
    refs = []

    async def read_code(resource, number=0, path="", ref="", offset=0, limit=8000):
        refs.append(ref)
        await asyncio.sleep(0)
        return json.dumps(
            {"ref": ref or ("a" if len(refs) == 1 else "b") * 40, "paths": [], "complete": True}
        )

    github.reads = read_code
    results = await asyncio.gather(service.read(token, "tree"), service.read(token, "tree"))
    assert {json.loads(value)["ref"] for value in results} == {"a" * 40}
    assert refs == ["", "a" * 40]


async def test_repository_scope_cannot_be_changed_under_queued_actions(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    assert draft.repository == "example/project"
    other = FakeGithub("different/repository")
    with pytest.raises(ValueError, match="another GitHub repository"):
        await ActionExecutor(database.unit, other).run_one()
    assert other.mutations == 0


async def test_published_draft_can_be_submitted_in_a_later_turn(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    await publish(database, request)
    request2, token2, _ = await begin(database, conversations, 11)
    submitted = await service.submit(token2, draft.id, draft.revision)
    assert submitted.revision > draft.revision and submitted.submitted
    await publish(database, request2)
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "ready"


async def test_silent_field_drop_is_reported_as_partial_without_recreating(database, conversations):
    class DropsLabelGithub(FakeGithub):
        async def mutate(self, draft):
            remote = await super().mutate(draft)
            remote = replace(remote, labels=())
            self.issue_data[remote.number] = remote
            return remote

    github = DropsLabelGithub()
    request, token, memory, github, service, draft = await prepare(
        database,
        conversations,
        ActionSpec("create_issue", (10,), title="Task", body="Acceptance", work_status="ready"),
        github,
    )
    await service.submit(token, draft.id, draft.revision)
    await publish(database, request)
    executor = ActionExecutor(database.unit, github)
    await executor.run_one()
    assert not await executor.run_one()
    async with database.unit() as unit:
        assert (await unit.actions.current(draft.id)).status == "succeeded"
        assert "Partial result" in await unit.connection.scalar(select(actions.c.detail))
    assert github.mutations == 1
