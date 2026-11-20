import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import event
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.database import Database
from peyda_pm_bot.adapters.storage.schema import checkpoint, inbox, outbox, versions
from peyda_pm_bot.adapters.storage.store import SqlStore
from peyda_pm_bot.adapters.telegram.fake import FakeTransport
from peyda_pm_bot.application.conversations.service import Conversations
from peyda_pm_bot.application.ports import PermanentFailure, TemporaryFailure, UnknownOutcome
from peyda_pm_bot.domain.conversations.events import IncomingUpdate
from peyda_pm_bot.runtime.delivery import Sender


async def rows(database, table):
    async with database.unit() as unit:
        return (await unit.connection.execute(select(table))).mappings().all()


async def test_transaction_rollback_inbox_and_checkpoint(database):
    with pytest.raises(RuntimeError):
        async with database.unit() as unit:
            await unit.store.ingest(event(), True)
            await unit.store.advance(11)
            raise RuntimeError("crash before commit")
    assert not await rows(database, inbox)
    assert (await rows(database, checkpoint))[0]["offset"] == 0


async def test_cancellation_waits_for_started_commit_and_returns_the_connection(
    database, monkeypatch
):
    commit_started = asyncio.Event()
    allow_commit = asyncio.Event()
    original_commit = AsyncConnection.commit

    async def delayed_commit(connection):
        commit_started.set()
        await allow_commit.wait()
        await original_commit(connection)

    async def advance_checkpoint():
        async with database.unit() as unit:
            await unit.incoming.advance(11)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncConnection, "commit", delayed_commit)
        transaction = asyncio.create_task(advance_checkpoint())
        try:
            await asyncio.wait_for(commit_started.wait(), 2)
            transaction.cancel()
            await asyncio.sleep(0)
            assert not transaction.done()
        finally:
            allow_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await transaction

    assert (await rows(database, checkpoint))[0]["offset"] == 11


async def test_final_batch_item_failure_does_not_advance(database, conversations, monkeypatch):
    original = SqlStore.ingest

    async def fail_second(self, item, accepted):
        if item.update_id == 11:
            raise RuntimeError("disk write failed")
        await original(self, item, accepted)

    with monkeypatch.context() as patch:
        patch.setattr(SqlStore, "ingest", fail_second)
        with pytest.raises(RuntimeError):
            await conversations.ingest([event(), event(11)])
    assert await conversations.offset() == 0
    assert not await rows(database, inbox)
    await conversations.ingest([event(), event(11)])
    assert await conversations.offset() == 12


async def test_replay_after_commit_before_ack_is_deduplicated(database, conversations):
    transport = FakeTransport([event()])
    await conversations.ingest(await transport.receive(0))
    # Simulate a server replay after the database committed but before the next getUpdates.
    await conversations.ingest(await transport.receive(0))
    await conversations.ingest(await transport.receive(await conversations.offset()))
    assert transport.offsets == [0, 0, 11]
    assert len(await rows(database, inbox)) == 1
    assert await conversations.process_one()
    assert not await conversations.process_one()
    assert len(await rows(database, versions)) == 1
    assert len(await rows(database, outbox)) == 1


async def test_versions_keep_identity_provenance_and_order(database, conversations):
    edited = event(11, text="corrected", is_edit=True, edited_at=datetime.now(UTC))
    await conversations.ingest([edited, event()])
    while await conversations.process_one():
        pass
    await conversations.ingest([edited])
    saved = await rows(database, versions)
    assert [row["update_id"] for row in saved] == [10, 11]
    assert '"reply_to_id":50' in saved[0]["payload"]
    assert '"speaker_id":7' in saved[0]["payload"]
    assert '"text":"corrected"' in saved[1]["payload"]
    assert len(await rows(database, outbox)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"chat_id": -999},
        {"topic_id": 2},
        {"topic_id": None},
    ],
)
async def test_filtered_inputs_advance_without_private_content(database, conversations, changes):
    await conversations.ingest([event(**changes), IncomingUpdate(11, "unsupported", None)])
    assert await conversations.offset() == 12
    assert not await conversations.process_one()
    assert all(row["payload"] is None for row in await rows(database, inbox))
    assert not await rows(database, versions)
    assert not await rows(database, outbox)


@pytest.mark.parametrize(
    "changes",
    [
        {"speaker_id": 8},
        {"speaker_id": None},
        {"sender_chat_id": -1001},
        {"text": "hello"},
        {"is_edit": True},
        {"text": None},
    ],
)
async def test_no_health_for_unauthorized_or_noncommand(database, conversations, changes):
    await conversations.ingest([event(**changes)])
    assert await conversations.process_one()
    assert len(await rows(database, versions)) == 1
    assert not await rows(database, outbox)


async def test_processing_rollback_includes_outbox(database, conversations, monkeypatch):
    await conversations.ingest([event()])
    original = SqlStore.finish_input

    async def fail_after_write(self, item, response):
        await original(self, item, response)
        raise RuntimeError("before processing commit")

    with monkeypatch.context() as patch:
        patch.setattr(SqlStore, "finish_input", fail_after_write)
        with pytest.raises(RuntimeError):
            await conversations.process_one()
    assert not await rows(database, versions)
    assert not await rows(database, outbox)
    assert (await rows(database, inbox))[0]["status"] == "pending"
    assert await conversations.process_one()
    assert len(await rows(database, outbox)) == 1


async def test_restart_recovers_unprocessed_and_pending_output(tmp_path):
    path = tmp_path / "restart.db"
    from peyda_pm_bot.application.conversations.service import ConversationPolicy

    policy = ConversationPolicy(-1001, 42, frozenset({7}))
    for stage in range(3):
        db = Database(path)
        try:
            await db.initialize(path, "test")
            service = Conversations(db.unit, policy)
            if stage == 0:
                await service.ingest([event()])
            elif stage == 1:
                assert await service.process_one()
            else:
                transport = FakeTransport()
                assert await Sender(db.unit, transport).send_one()
                assert len(transport.sent) == 1
                assert transport.sent[0].topic_id == 42
                assert transport.sent[0].reply_to_id == 100
                assert (await rows(db, outbox))[0]["status"] == "sent"
        finally:
            await db.close()


async def test_unknown_sending_claim_recovered_without_retry(database, conversations):
    await conversations.ingest([event()])
    await conversations.process_one()
    async with database.unit() as unit:
        assert await unit.store.claim_delivery(datetime.now(UTC))
    async with database.unit() as unit:
        await unit.store.recover("test")
    transport = FakeTransport()
    assert not await Sender(database.unit, transport).send_one()
    assert (await rows(database, outbox))[0]["status"] == "unknown"


@pytest.mark.parametrize(
    "error,status",
    [
        (UnknownOutcome(), "unknown"),
        (TimeoutError(), "unknown"),
        (PermanentFailure(), "failed"),
    ],
)
async def test_send_failures_are_not_blindly_retried(database, conversations, error, status):
    await conversations.ingest([event()])
    await conversations.process_one()
    transport = FakeTransport()
    transport.send_errors.append(error)
    sender = Sender(database.unit, transport)
    assert await sender.send_one()
    assert not await sender.send_one()
    assert (await rows(database, outbox))[0]["status"] == status


async def test_retry_after_and_attempt_limit(database, conversations):
    await conversations.ingest([event()])
    await conversations.process_one()
    now = datetime.now(UTC)
    transport = FakeTransport()
    transport.send_errors.extend([TemporaryFailure(60), TemporaryFailure(), TemporaryFailure()])
    sender = Sender(database.unit, transport, clock=lambda: now)
    assert await sender.send_one()
    assert not await sender.send_one()
    now += timedelta(seconds=59)
    assert not await sender.send_one()
    now += timedelta(seconds=1)
    assert await sender.send_one()
    assert not await sender.send_one()
    now += timedelta(seconds=2)
    assert await sender.send_one()
    row = (await rows(database, outbox))[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert len(await rows(database, versions)) == 1


async def test_concurrent_claims_do_not_send_twice(database, conversations):
    await conversations.ingest([event()])
    await conversations.process_one()
    transport = FakeTransport()
    first = Sender(database.unit, transport)
    second = Sender(database.unit, transport)
    assert sorted(await asyncio.gather(first.send_one(), second.send_one())) == [False, True]
    assert len(transport.sent) == 1


async def test_sqlite_pragmas_and_project_binding(database):
    async with database.unit() as unit:
        for pragma, expected in [
            ("foreign_keys", 1),
            ("journal_mode", "wal"),
            ("busy_timeout", 5000),
        ]:
            assert await unit.connection.scalar(text(f"PRAGMA {pragma}")) == expected
        with pytest.raises(ValueError, match="another project"):
            await unit.store.recover("other-project")


async def test_migrations_upgrade_downgrade_upgrade(tmp_path):
    from alembic import command
    from alembic.config import Config

    path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")
    db = Database(path)
    try:
        await db.initialize(path, "test")
        assert not await rows(db, inbox)
    finally:
        await db.close()


async def test_successful_send_then_commit_failure_stays_unknown_on_restart(
    database, conversations, monkeypatch
):
    await conversations.ingest([event()])
    await conversations.process_one()
    transport = FakeTransport()

    async def fail_result(*args):
        raise RuntimeError("result persistence failed")

    with monkeypatch.context() as patch:
        patch.setattr(SqlStore, "finish_delivery", fail_result)
        with pytest.raises(RuntimeError):
            await Sender(database.unit, transport).send_one()
    assert len(transport.sent) == 1
    async with database.unit() as unit:
        await unit.store.recover("test")
    assert not await Sender(database.unit, transport).send_one()
    assert len(transport.sent) == 1
    assert (await rows(database, outbox))[0]["status"] == "unknown"


async def test_migrated_schema_matches_storage_metadata(database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from peyda_pm_bot.adapters.storage.schema import metadata

    async with database.unit() as unit:
        differences = await unit.connection.run_sync(
            lambda connection: compare_metadata(MigrationContext.configure(connection), metadata)
        )
    assert differences == []
