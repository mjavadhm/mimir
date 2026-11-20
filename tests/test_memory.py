"""Memory is source backed, scoped to a live turn, and committed atomically."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import event
from sqlalchemy import func, select
from test_phase_two import batch

from peyda_pm_bot.adapters.storage.database import Database
from peyda_pm_bot.adapters.storage.schema import jobs, memory_turns, outbox
from peyda_pm_bot.application.memory.policy import make_policy
from peyda_pm_bot.application.memory.service import MemoryService
from peyda_pm_bot.domain.agent.models import AgentResult
from peyda_pm_bot.domain.memory.models import MemoryRecord, normalize


async def begin(database, conversations, index=10):
    await batch(database, conversations, [event(index, text="تصمیم ورود با شماره باشد")])
    async with database.unit() as unit:
        request = await unit.queue.claim(105)
        token = await unit.memory.begin(request, make_policy(), "test")
    return request, token, MemoryService(database.unit, "test")


async def finalize(database, request, result=None):
    async with database.unit() as unit:
        assert await unit.queue.wanted(request)
        changed = await unit.memory.commit(request)
        await unit.queue.complete(request, result or AgentResult("silent"))
        await unit.outgoing.memory_notice(request, changed)


async def case_and_decision(service, token, source=10):
    case = await service.prepare(
        token,
        "case",
        MemoryRecord("case", "ورود", aliases=("ورود كاربري",), source_update_ids=(source,)),
    )
    decision = await service.prepare(
        token,
        "decision",
        MemoryRecord(
            "note",
            "روش ورود",
            "با شماره تلفن",
            category="decision",
            case_ids=(case.record.id,),
            source_update_ids=(source,),
            attributed_user_ids=(7,),
        ),
    )
    return case, decision


async def test_staged_overlay_and_commit_notice(database, conversations):
    request, token, service = await begin(database, conversations)
    case, decision = await case_and_decision(service, token)
    assert (await service.read(token, case.record.id)).items[1].staged
    async with database.unit() as unit:
        assert not await unit.memory.views()
    await finalize(database, request)
    async with database.unit() as unit:
        assert len(await unit.memory.views()) == 2
        assert (
            await unit.connection.scalar(
                select(func.count()).select_from(outbox).where(outbox.c.purpose == "memory")
            )
            == 1
        )
    with pytest.raises(ValueError, match="closed"):
        await service.read(token, case.record.id)


async def test_operation_idempotency_and_conflict(database, conversations):
    request, token, service = await begin(database, conversations)
    record = MemoryRecord("case", "ورود")
    first = await service.prepare(token, "key", record)
    assert await service.prepare(token, "key", record) == first
    with pytest.raises(ValueError, match="different input"):
        await service.prepare(token, "key", replace(record, title="other"))
    with pytest.raises(ValueError, match="Stale"):
        await service.prepare(token, "stale", replace(first.record, title="updated"), 0)


@pytest.mark.parametrize("mode", ["cancelled", "unknown", "superseded"])
async def test_inactive_turn_cannot_read_or_write(database, conversations, mode):
    request, token, service = await begin(database, conversations)
    await case_and_decision(service, token)
    async with database.unit() as unit:
        await unit.connection.execute(
            jobs.update().where(jobs.c.id == request.job_id).values(status=mode)
        )
    with pytest.raises(ValueError, match="no longer active"):
        await service.search(token, "ورود")
    with pytest.raises(ValueError):
        await service.prepare(token, "late", MemoryRecord("case", "late"))
    async with database.unit() as unit:
        assert not await unit.memory.views()


async def test_project_and_forged_source_rejected(database, conversations):
    request, token, service = await begin(database, conversations)
    with pytest.raises(ValueError):
        await MemoryService(database.unit, "another").search(token, "ورود")
    case = await service.prepare(token, "case", MemoryRecord("case", "ورود"))
    with pytest.raises(ValueError, match="source"):
        await service.prepare(
            token,
            "fake",
            MemoryRecord(
                "note",
                "fake",
                category="decision",
                case_ids=(case.record.id,),
                source_update_ids=(999,),
            ),
        )
    with pytest.raises(ValueError, match="Attribution"):
        await service.prepare(
            token,
            "speaker",
            MemoryRecord(
                "note",
                "fake",
                category="decision",
                case_ids=(case.record.id,),
                source_update_ids=(10,),
                attributed_user_ids=(99,),
            ),
        )


async def test_commit_failure_rolls_back_memory_job_and_notice(database, conversations):
    request, token, service = await begin(database, conversations)
    await case_and_decision(service, token)
    with pytest.raises(RuntimeError):
        async with database.unit() as unit:
            changed = await unit.memory.commit(request)
            await unit.queue.complete(request, AgentResult("silent"))
            await unit.outgoing.memory_notice(request, changed)
            raise RuntimeError("crash before commit")
    async with database.unit() as unit:
        assert not await unit.memory.views()
        assert await unit.connection.scalar(select(jobs.c.status)) == "running"
        assert not (await unit.connection.execute(select(outbox))).first()
    await finalize(database, request)


async def test_history_soft_removal_and_restart(database, conversations):
    request, token, service = await begin(database, conversations)
    case, decision = await case_and_decision(service, token)
    await finalize(database, request)
    request, token, service = await begin(database, conversations, 11)
    await service.prepare(
        token,
        "remove",
        replace(decision.record, status="inactive", reason="تأیید شد که کنار گذاشته شود"),
        1,
    )
    await finalize(database, request)
    path = Path(database.engine.url.database)
    await database.close()
    restored = Database(path)
    try:
        await restored.initialize(path, "test")
        async with restored.unit() as unit:
            history = await unit.memory.history(decision.record.id)
            assert [view.record.status for view in history] == ["inactive", "active"]
            assert history[1].superseded
            assert history[1].record.source_update_ids == (10,)
    finally:
        await restored.close()


async def test_farsi_search_alias_and_pagination(database, conversations):
    request, token, service = await begin(database, conversations)
    for index in range(3):
        await service.prepare(
            token, str(index), MemoryRecord("case", f"ورود {index}", aliases=("ورود کاربری",))
        )
    found = await service.search(token, "ورود كاربري", limit=1)
    assert len(found.items) == 1 and found.next_cursor
    next_page = await service.search(token, "ورود کاربری", cursor=found.next_cursor, limit=1)
    assert next_page.items[0].record.id != found.items[0].record.id
    with pytest.raises(ValueError, match="cursor"):
        await service.search(token, "different", cursor=found.next_cursor)
    assert normalize("ورود\u200cكاربري") == normalize("ورود کاربری")


async def test_sources_preserve_original_and_mark_stale(database, conversations):
    request, token, service = await begin(database, conversations)
    case, decision = await case_and_decision(service, token)
    summary = await service.prepare(
        token,
        "summary",
        MemoryRecord(
            "summary", "خلاصه", "ورود با شماره", case_ids=(case.record.id,), source_update_ids=(10,)
        ),
    )
    await finalize(database, request)
    await batch(database, conversations, [event(11, text="تصحیح شد", is_edit=True)], 110)
    async with database.unit() as unit:
        views = {view.record.id: view for view in await unit.memory.views()}
        assert views[decision.record.id].needs_review
        assert views[summary.record.id].needs_review
        evidence = await unit.memory.evidence((10, 11))
        assert evidence[0].text == "تصمیم ورود با شماره باشد" and evidence[0].superseded


async def test_multi_case_link_and_correction(database, conversations):
    request, token, service = await begin(database, conversations)
    one = await service.prepare(token, "one", MemoryRecord("case", "ورود"))
    two = await service.prepare(token, "two", MemoryRecord("case", "پرداخت"))
    link = await service.prepare(
        token,
        "link",
        MemoryRecord(
            "link", "رابطه پیام", case_ids=(one.record.id, two.record.id), source_update_ids=(10,)
        ),
    )
    await service.prepare(
        token,
        "correct",
        replace(link.record, case_ids=(two.record.id,), reason="موضوع ورود نبود"),
        1,
    )
    await finalize(database, request)
    async with database.unit() as unit:
        history = await unit.memory.history(link.record.id)
        assert history[0].record.case_ids == (two.record.id,)
        assert len(history[1].record.case_ids) == 2


async def test_worker_commits_memory_and_audits_policy(database, conversations):
    from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
    from peyda_pm_bot.application.agent.worker import AgentWorker

    service = MemoryService(database.unit, "test")

    class MemoryAgent(FakeAgent):
        async def run(self, request):
            assert request.memory_token and request.memory_policy_version
            assert "دو تأیید" in request.memory_policy
            assert request.memory_token not in repr(request)
            await case_and_decision(service, request.memory_token)
            yield AgentResult("silent")

    await batch(database, conversations, [event(text="تصمیم ورود با شماره باشد")])
    await AgentWorker(database.unit, MemoryAgent()).run_one()
    async with database.unit() as unit:
        assert len(await unit.memory.views()) == 2
        turn = (await unit.connection.execute(select(memory_turns))).mappings().one()
        assert turn["state"] == "committed" and turn["policy_version"] == make_policy().version
        assert await unit.connection.scalar(select(jobs.c.status)) == "succeeded"
        assert (
            await unit.connection.scalar(
                select(func.count()).select_from(outbox).where(outbox.c.purpose == "memory")
            )
            == 1
        )


async def test_invalid_agent_output_does_not_publish_staged_memory(database, conversations):
    from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
    from peyda_pm_bot.application.agent.worker import AgentWorker

    service = MemoryService(database.unit, "test")

    class BrokenAgent(FakeAgent):
        async def run(self, request):
            await case_and_decision(service, request.memory_token)
            yield AgentResult("reply", "invalid target", 999)

    await batch(database, conversations, [event(text="تصمیم ورود با شماره باشد")])
    await AgentWorker(database.unit, BrokenAgent()).run_one()
    async with database.unit() as unit:
        assert not await unit.memory.views()
        assert await unit.connection.scalar(select(jobs.c.status)) == "failed"
        assert (
            await unit.connection.scalar(
                select(func.count()).select_from(outbox).where(outbox.c.purpose == "memory")
            )
            == 0
        )


async def test_summary_becomes_stale_after_link_correction(database, conversations):
    request, token, service = await begin(database, conversations)
    case, decision = await case_and_decision(service, token)
    second = await service.prepare(token, "second", MemoryRecord("case", "پرداخت"))
    link = await service.prepare(
        token,
        "link",
        MemoryRecord("link", "پیوند", case_ids=(case.record.id,), source_update_ids=(10,)),
    )
    summary = await service.prepare(
        token,
        "summary",
        MemoryRecord(
            "summary", "خلاصه", "توضیح", case_ids=(case.record.id,), source_update_ids=(10,)
        ),
    )
    await finalize(database, request)
    request, token, service = await begin(database, conversations, 11)
    await service.prepare(
        token,
        "correction",
        replace(link.record, case_ids=(second.record.id,), reason="موضوع دیگری بود"),
        1,
    )
    await finalize(database, request)
    async with database.unit() as unit:
        assert {view.record.id: view for view in await unit.memory.views()}[
            summary.record.id
        ].needs_review


async def test_tool_page_budget_and_cursor_invalidated_by_changes(database, conversations):
    import json
    from dataclasses import asdict

    request, token, service = await begin(database, conversations)
    for index in range(3):
        await service.prepare(
            token, str(index), MemoryRecord("case", f"پرونده {index}", "x" * 7000)
        )
    found = await service.search(token, "پرونده", limit=20)
    assert len(json.dumps(asdict(found), ensure_ascii=False)) <= 12000
    assert found.next_cursor
    await service.prepare(token, "new", MemoryRecord("case", "پرونده تازه"))
    with pytest.raises(ValueError, match="cursor"):
        await service.search(token, "پرونده", cursor=found.next_cursor)


async def test_fts_farsi_probe():
    import sqlite3

    def probe():
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE sample USING fts5(text)")
            connection.execute("INSERT INTO sample VALUES (?)", ("ورود کاربری",))
            raw = connection.execute(
                "SELECT count(*) FROM sample WHERE sample MATCH ?", ("كاربري",)
            ).fetchone()[0]
            normalized = connection.execute(
                "SELECT count(*) FROM sample WHERE sample MATCH ?", (normalize("كاربري"),)
            ).fetchone()[0]
            return raw, normalized

    try:
        assert await asyncio.to_thread(probe) == (0, 1)
    except sqlite3.OperationalError as error:
        if "no such module: fts5" not in str(error):
            raise
        pytest.skip("SQLite build does not provide optional FTS5")


async def test_retry_grant_cannot_see_old_staged_changes(database, conversations):
    request, token, service = await begin(database, conversations)
    await case_and_decision(service, token)
    async with database.unit() as unit:
        await unit.queue.fail(request, "unknown")
        await unit.queue.control("/retry", str(request.job_id))
        retry = await unit.queue.claim(200)
        fresh = await unit.memory.begin(retry, make_policy(), "test")
    with pytest.raises(ValueError):
        await service.search(token, "ورود")
    assert not (await service.search(fresh, "ورود")).items


async def test_case_draft_does_not_touch_repository_files(database, conversations, tmp_path):
    request, token, service = await begin(database, conversations)
    case = await service.prepare(token, "case", MemoryRecord("case", "معماری"))
    before = list(tmp_path.iterdir())
    await service.prepare(
        token,
        "draft",
        MemoryRecord(
            "architecture_draft",
            "ADR پیشنهادی",
            "روش پیشنهادی",
            case_ids=(case.record.id,),
            source_update_ids=(10,),
        ),
    )
    await finalize(database, request)
    assert list(tmp_path.iterdir()) == before


async def test_summary_coverage_and_original_speaker_metadata(database, conversations):
    request, token, service = await begin(database, conversations)
    sources = await service.sources(token, (10,))
    assert sources.items[0].speaker_name == "Admin"
    assert sources.items[0].topic_id == 42
    with pytest.raises(ValueError):
        await service.prepare(
            token, "bad-summary", MemoryRecord("summary", "بدون پوشش", case_ids=("unknown",))
        )
