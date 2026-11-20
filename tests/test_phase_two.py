import asyncio
import sys
from pathlib import Path

import pytest
from conftest import event
from sqlalchemy import func, select

from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
from peyda_pm_bot.adapters.antigravity.permissions import ReadPolicy
from peyda_pm_bot.adapters.storage.schema import jobs, outbox
from peyda_pm_bot.adapters.telegram.fake import FakeTransport
from peyda_pm_bot.application.agent.worker import AgentWorker
from peyda_pm_bot.application.conversations.service import Conversations
from peyda_pm_bot.domain.agent.models import AgentResult
from peyda_pm_bot.runtime.delivery import Sender
from peyda_pm_bot.runtime.process import ProcessOwner


async def batch(database, conversations, updates, now=100):
    service = Conversations(
        database.unit, conversations.policy, queue_enabled=True, clock=lambda: now
    )
    await service.ingest(updates)
    while await service.process_one():
        pass
    return service


async def test_batch_deadline_and_claim_exclusive(database, conversations):
    await batch(database, conversations, [event(text="first")])
    await batch(
        database, conversations, [event(11, message_id=101, speaker_id=8, text="second")], 103
    )
    async with database.unit() as unit:
        assert await unit.queue.claim(106) is None
        request = await unit.queue.claim(107)
        assert [item.message.speaker_id for item in request.messages] == [7, 8]
        assert await unit.queue.claim(200) is None
        assert request.revision == 2


async def test_continuous_messages_obey_ceiling(database, conversations):
    for index in range(5):
        await batch(
            database,
            conversations,
            [event(10 + index, message_id=100 + index, text="hello")],
            100 + index * 3,
        )
    async with database.unit() as unit:
        request = await unit.queue.claim(112)
        assert request is not None
        assert len(request.messages) == 4


async def test_edit_revises_pending_then_supersedes_running(database, conversations):
    await batch(database, conversations, [event(text="old")])
    await batch(database, conversations, [event(11, text="new", is_edit=True)], 101)
    async with database.unit() as unit:
        assert await unit.queue.claim(104) is None
        request = await unit.queue.claim(105)
        assert request.messages[0].message.text == "new"
    await batch(database, conversations, [event(12, text="newest", is_edit=True)], 106)
    async with database.unit() as unit:
        assert not await unit.queue.complete(request, AgentResult("reply", "obsolete"))
        assert await unit.queue.claim(111) is not None


async def test_job_output_atomic_multipart_and_sender_order(database, conversations):
    await batch(database, conversations, [event(text="question")])
    async with database.unit() as unit:
        request = await unit.queue.claim(105)
        await unit.queue.complete(request, AgentResult("reply", "a" * 7001))
    transport = FakeTransport()
    sender = Sender(database.unit, transport)
    while await sender.send_one():
        pass
    assert [len(item.text) for item in transport.sent] == [3500, 3500, 1]
    async with database.unit() as unit:
        assert not await unit.queue.complete(request, AgentResult("reply", "duplicate"))


async def test_recovery_unknown_blocks_and_explicit_retry(database, conversations):
    await batch(database, conversations, [event(text="question")])
    async with database.unit() as unit:
        request = await unit.queue.claim(105)
    async with database.unit() as unit:
        await unit.queue.recover()
        assert (await unit.queue.state()).blocked
        assert await unit.queue.claim(200) is None
        await unit.queue.control("/retry", str(request.job_id))
        assert not (await unit.queue.state()).blocked
        retried = await unit.queue.claim(200)
        assert retried.revision > request.revision


async def test_silent_job_and_shared_session(database, conversations):
    fake = FakeAgent()
    worker = AgentWorker(database.unit, fake)
    for index in range(2):
        await batch(
            database, conversations, [event(10 + index, text="context", message_id=100 + index)]
        )
        assert await worker.run_one()
    assert fake.peak == 1
    assert len(fake.requests) == 2
    async with database.unit() as unit:
        state = await unit.queue.state()
        assert state.session_id == "fake-shared-session"
        assert state.generation == 1
        assert (
            await unit.connection.scalar(
                select(func.count()).select_from(outbox).where(outbox.c.purpose == "reply")
            )
            == 0
        )


async def test_invalid_result_target_rejected(database, conversations):
    await batch(database, conversations, [event(text="hi")])
    fake = FakeAgent()
    fake.results = [AgentResult("reply", "answer", 999)]
    await AgentWorker(database.unit, fake).run_one()
    async with database.unit() as unit:
        assert await unit.connection.scalar(select(jobs.c.status)) == "failed"


async def test_failure_notice_is_durable_deduplicated_and_does_not_block_cancelled_job(
    database,
    conversations,
):
    await batch(database, conversations, [event(text="question")])
    async with database.unit() as unit:
        request = await unit.queue.claim(105)
    async with database.unit() as unit:
        await unit.queue.fail(request, "failed")
        await unit.queue.fail(request, "failed")
    async with database.unit() as unit:
        notices = (
            (
                await unit.connection.execute(
                    select(outbox).where(outbox.c.purpose == "job_failure"),
                )
            )
            .mappings()
            .all()
        )
        assert len(notices) == 1
        assert f"/retry {request.job_id}" in notices[0]["text"]
        assert notices[0]["reply_to_id"] == 100
        await unit.queue.control("/retry", str(request.job_id))
        retried = await unit.queue.claim(200)
        await unit.queue.control("/cancel", str(request.job_id))
        await unit.queue.fail(retried, "unknown")
        assert not (await unit.queue.state()).blocked


async def test_cancel_active_job_stops_before_next(database, conversations):
    started = asyncio.Event()

    class BlockingAgent(FakeAgent):
        async def run(self, request):
            started.set()
            await asyncio.Event().wait()
            yield AgentResult("reply", "late")

    await batch(database, conversations, [event(text="question")])
    worker = AgentWorker(database.unit, BlockingAgent())
    task = asyncio.create_task(worker.run_one())
    await asyncio.wait_for(started.wait(), 5)
    async with database.unit() as unit:
        job_id = await unit.connection.scalar(select(jobs.c.id))
        await unit.queue.control("/cancel", str(job_id))
    await asyncio.wait_for(task, 5)
    assert not worker.connected
    async with database.unit() as unit:
        assert await unit.connection.scalar(select(jobs.c.status)) == "cancelled"


async def test_control_auth_and_turbo_boundary(database, conversations):
    await batch(database, conversations, [event(text="/turbo on", speaker_id=8)])
    async with database.unit() as unit:
        assert not (await unit.queue.state()).turbo_requested
    await batch(database, conversations, [event(11, text="/turbo on")])
    fake = FakeAgent()
    await AgentWorker(database.unit, fake).run_one()
    async with database.unit() as unit:
        assert (await unit.queue.state()).turbo_effective
    assert fake.turbo


async def test_card_reuses_message_and_throttles(database, conversations):
    await batch(database, conversations, [event(text="hello")])
    async with database.unit() as unit:
        await unit.queue.card("working", 100)
        await unit.queue.card("reading", 101)
    transport = FakeTransport()
    sender = Sender(database.unit, transport)
    await sender.send_one()
    async with database.unit() as unit:
        await unit.queue.card("ready", 104)
    await sender.send_one()
    assert len(transport.sent) == 2
    assert transport.sent[1].target_id == 1


def test_read_policy_blocks_symlink_and_secrets(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("not readable")
    (root / "link").symlink_to(secret)
    (root / "src").mkdir()
    (root / "src/main.py").write_text("print('project code')")
    (root / ".env").symlink_to(root / "src/main.py")
    policy = ReadPolicy(root)
    assert policy.allowed("read", "src/main.py")
    for operation, target in [
        ("read", "link"),
        ("read", ".env"),
        ("read", "src"),
        ("read", "../secret"),
        ("write", "src/main.py"),
        ("shell", "ls"),
    ]:
        assert not policy.allowed(operation, target)


async def test_subprocess_shutdown(tmp_path):
    owner = ProcessOwner([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path)
    process = await owner.start()
    assert process.returncode is None
    await owner.close()
    assert process.returncode is not None


async def test_acp_sdk_wire_and_resume(tmp_path):
    from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
    from peyda_pm_bot.domain.agent.models import AgentRequest

    script = Path(__file__).parent / "fixtures" / "acp_server.py"
    owner = ProcessOwner([sys.executable, str(script)], tmp_path)
    agent = AcpAgent(owner, tmp_path, validated_environment=True)
    try:
        async with asyncio.timeout(10):
            session = await agent.connect(None)
            assert session == "offline-acp-session"
            results = [item async for item in agent.run(AgentRequest(1, 1, 1, (event(),)))]
            assert results[-1].text == "offline response"
            assert not await agent.set_turbo(True)
            await agent.close()
            assert await agent.connect(session) == session
    finally:
        await agent.close()


async def test_acp_gate_and_permission_identity(tmp_path):
    from acp import schema

    from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent

    agent = AcpAgent(ProcessOwner(["unused"], tmp_path), tmp_path)
    with pytest.raises(RuntimeError, match="live validation"):
        await agent.connect(None)
    agent.session_id = "active"
    agent.in_turn = True
    options = [
        schema.PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
        schema.PermissionOption(option_id="deny", name="Deny", kind="reject_once"),
    ]
    tool = schema.ToolCallUpdate(
        tool_call_id="t",
        name="read_file",
        kind="read",
        locations=[schema.ToolCallLocation(path=str(tmp_path / "file.py"))],
    )
    (tmp_path / "file.py").touch()
    denied = await agent.request_permission("wrong-session", tool, options)
    assert denied.outcome.option_id == "deny"
    accepted = await agent.request_permission("active", tool, options)
    assert accepted.outcome.option_id == "allow"


async def test_worker_concurrent_calls_serialized(database, conversations):
    await batch(database, conversations, [event(text="question")])
    fake = FakeAgent()
    worker = AgentWorker(database.unit, fake)
    assert sorted(await asyncio.gather(worker.run_one(), worker.run_one())) == [False, True]
    assert fake.peak == 1


async def test_batch_size_limit(database, conversations):
    await batch(
        database, conversations, [event(10 + i, message_id=100 + i, text="x") for i in range(51)]
    )
    async with database.unit() as unit:
        assert await unit.connection.scalar(select(func.count()).select_from(jobs)) == 2


async def test_process_identity_and_duplicate_start(tmp_path):
    owner = ProcessOwner([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path)
    owner.identity_path = tmp_path / "identity.json"
    try:
        await owner.start()
        assert owner.identity_path.exists()
        with pytest.raises(RuntimeError, match="already running"):
            await owner.start()
    finally:
        await owner.close()
    assert not owner.identity_path.exists()


async def test_parent_death_stops_guardian_and_agent(tmp_path):
    import os

    script = """
import asyncio, sys
from pathlib import Path
from peyda_pm_bot.runtime.process import ProcessOwner
async def main():
    command='import os,time; print(os.getpid(),flush=True); time.sleep(60)'
    owner=ProcessOwner([sys.executable,'-u','-c',command],Path(sys.argv[1]))
    process=await owner.start()
    line=await process.stdout.readline()
    print(process.pid,line.decode().strip(),flush=True)
    await asyncio.sleep(60)
asyncio.run(main())
"""
    parent = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", script, str(tmp_path), stdout=asyncio.subprocess.PIPE
    )
    pids = []
    try:
        async with asyncio.timeout(10):
            pids = [int(value) for value in (await parent.stdout.readline()).split()]
        assert len(pids) == 2
        parent.kill()
        await parent.wait()
        for _ in range(100):
            active = []
            for pid in pids:
                stat = Path(f"/proc/{pid}/stat")
                if await asyncio.to_thread(_process_active, stat):
                    active.append(pid)
            if not active:
                break
            await asyncio.sleep(0.02)
        assert not active
    finally:
        if parent.returncode is None:
            parent.kill()
            await parent.wait()
        for pid in pids:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


async def test_agent_timeout_is_unknown(database, conversations):
    class SlowAgent(FakeAgent):
        async def run(self, request):
            await asyncio.Event().wait()
            yield AgentResult("silent")

    await batch(database, conversations, [event(text="question")])
    worker = AgentWorker(database.unit, SlowAgent(), timeout=0.01)
    await worker.run_one()
    async with database.unit() as unit:
        assert await unit.connection.scalar(select(jobs.c.status)) == "unknown"
        assert (await unit.queue.state()).blocked


def _process_active(stat):
    try:
        return stat.read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


async def test_failed_card_creation_does_not_loop(database, conversations):
    from peyda_pm_bot.application.ports import PermanentFailure

    await batch(database, conversations, [event(text="hello")])
    async with database.unit() as unit:
        await unit.queue.card("working", 100)
    transport = FakeTransport()
    transport.send_errors.append(PermanentFailure())
    await Sender(database.unit, transport).send_one()
    async with database.unit() as unit:
        await unit.queue.card("ready", 104)
        assert (
            await unit.connection.scalar(
                select(func.count()).select_from(outbox).where(outbox.c.purpose == "card")
            )
            == 1
        )
