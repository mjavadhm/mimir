"""Offset advances only after commit, and an unknown health send is not repeated."""

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from peyda_pm_bot.adapters.storage.engine import open_unit
from peyda_pm_bot.adapters.storage.models import OutboxRow
from peyda_pm_bot.application.conversations.deliver import deliver_one
from peyda_pm_bot.application.conversations.ports import (
    DeliveryRejected,
    DeliveryRetry,
    DeliveryUnknown,
    IngestStores,
)
from peyda_pm_bot.domain.conversations.models import Allowlist, IncomingUpdate
from peyda_pm_bot.runtime.cycle import Opener, run_cycle
from tests.conftest import NOW, Database, incoming


class ScriptedSource:
    def __init__(self, batches: list[list[IncomingUpdate]]) -> None:
        self._batches = list(batches)
        self.offsets: list[int | None] = []

    async def fetch(self, offset: int | None) -> list[IncomingUpdate]:
        self.offsets.append(offset)
        if not self._batches:
            return []
        return self._batches.pop(0)


class Sender:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def send_health(self, chat_id: int, topic_id: int) -> int:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return 42


class Notice:
    def __init__(self, attempt_count: int) -> None:
        self.update_id = 1
        self.attempt_count = attempt_count


async def test_deliver_one_keeps_unknown_and_caps_retries() -> None:
    unknown = await deliver_one(Notice(0), Sender(DeliveryUnknown("timeout")), -100, 7)
    assert unknown.status == "unknown"
    rejected = await deliver_one(Notice(0), Sender(DeliveryRejected("no")), -100, 7)
    assert rejected.status == "rejected"
    pending = await deliver_one(Notice(0), Sender(DeliveryRetry(2)), -100, 7)
    assert pending.status == "pending"
    assert pending.attempt_count == 1
    exhausted = await deliver_one(Notice(2), Sender(DeliveryRetry(2)), -100, 7)
    assert exhausted.status == "rejected"
    assert exhausted.attempt_count == 3
    sent = await deliver_one(Notice(0), Sender(), -100, 7)
    assert sent.status == "sent"
    assert sent.external_message_id == 42


async def test_offset_is_passed_only_after_commit(database: Database, allowlist: Allowlist) -> None:
    _path, factory = database
    source = ScriptedSource([[incoming(update_id=4, text="hello")], []])
    sender = Sender()
    first = await run_cycle(source, _as_opener(factory), sender, allowlist, NOW)
    second = await run_cycle(source, _as_opener(factory), sender, allowlist, NOW)
    assert [item.duplicate for item in first] == [False]
    assert second == []
    assert source.offsets == [None, 5]
    assert sender.calls == 0


async def test_failed_commit_leaves_the_offset_at_zero(
    database: Database, allowlist: Allowlist
) -> None:
    _path, factory = database
    source = ScriptedSource([[incoming(text="again")], [incoming(text="again")]])

    @asynccontextmanager
    async def broken() -> AsyncIterator[object]:
        async with open_unit(factory) as unit:

            async def boom() -> None:
                raise RuntimeError("disk")

            unit.commit = boom  # type: ignore[method-assign]
            yield unit

    try:
        await run_cycle(source, broken, Sender(), allowlist, NOW)
    except RuntimeError as exc:
        assert str(exc) == "disk"
    else:
        raise AssertionError("commit should fail")

    stored = await run_cycle(source, _as_opener(factory), Sender(), allowlist, NOW)
    assert source.offsets == [None, None]
    assert stored[0].duplicate is False
    async with open_unit(factory) as unit:
        assert await unit.checkpoint.current_offset() == 2
        assert await unit.update_ids() == [1]


async def test_unknown_health_send_is_not_repeated(
    database: Database, allowlist: Allowlist
) -> None:
    _path, factory = database
    source = ScriptedSource([[incoming(text="/health")], [], []])
    sender = Sender(DeliveryUnknown("timeout"))
    first = await run_cycle(source, _as_opener(factory), sender, allowlist, NOW)
    second = await run_cycle(source, _as_opener(factory), sender, allowlist, NOW)
    assert first[0].health_pending is True
    assert second == []
    assert sender.calls == 1
    async with open_unit(factory) as unit:
        row = await unit.session.scalar(select(OutboxRow).where(OutboxRow.update_id == 1))
        assert row is not None
        status = row.status
        attempts = row.attempt_count
    assert status == "unknown"
    assert attempts == 1


async def test_health_retry_stops_at_the_cap(database: Database, allowlist: Allowlist) -> None:
    _path, factory = database
    source = ScriptedSource([[incoming(text="/health")], [], [], []])
    sender = Sender(DeliveryRetry(1))
    for _ in range(4):
        await run_cycle(source, _as_opener(factory), sender, allowlist, NOW)
    assert sender.calls == 3
    async with open_unit(factory) as unit:
        row = await unit.session.scalar(select(OutboxRow).where(OutboxRow.update_id == 1))
        assert row is not None
        assert row.status == "rejected"
        assert row.attempt_count == 3


async def test_cycle_log_does_not_include_message_text(
    database: Database, allowlist: Allowlist, caplog: pytest.LogCaptureFixture
) -> None:
    _path, factory = database
    secret = "visible-text-should-stay-out-of-logs"
    source = ScriptedSource([[incoming(text=secret)]])
    with caplog.at_level(logging.INFO, logger="peyda.ingest"):
        await run_cycle(source, _as_opener(factory), Sender(), allowlist, NOW)
    assert secret not in caplog.text
    assert "update_id=1" in caplog.text


def _as_opener(factory: async_sessionmaker[AsyncSession]) -> Opener:
    def open_stores() -> AbstractAsyncContextManager[IngestStores]:
        return open_unit(factory)

    return open_stores
