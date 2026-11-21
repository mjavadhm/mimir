"""One ingest cycle: read the offset, fetch, commit, then send health."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from peyda_pm_bot.application.conversations.deliver import deliver_one
from peyda_pm_bot.application.conversations.ingest import IngestOutcome, ingest_batch
from peyda_pm_bot.application.conversations.ports import (
    HealthSender,
    IngestStores,
    UpdateSource,
)
from peyda_pm_bot.domain.conversations.models import Allowlist

logger = logging.getLogger("peyda.ingest")

Opener = Callable[[], AbstractAsyncContextManager[IngestStores]]


async def run_cycle(
    source: UpdateSource,
    opener: Opener,
    sender: HealthSender,
    allowlist: Allowlist,
    received_at: datetime,
) -> list[IngestOutcome]:
    """Fetch outside the transaction and advance the offset only after commit."""

    async with opener() as unit:
        current = await unit.checkpoint.current_offset()
    offset = None if current == 0 else current
    updates = list(await source.fetch(offset))
    async with opener() as unit:
        outcomes = await ingest_batch(unit, updates, allowlist, received_at)
        await unit.commit()
    for outcome in outcomes:
        logger.info(
            "update_id=%s duplicate=%s admission=%s",
            outcome.update_id,
            outcome.duplicate,
            None if outcome.admission is None else outcome.admission.value,
        )
    await _deliver(opener, sender, allowlist)
    return outcomes


async def _deliver(opener: Opener, sender: HealthSender, allowlist: Allowlist) -> None:
    async with opener() as unit:
        pending = list(await unit.outbox.pending_health())
    for notice in pending:
        result = await deliver_one(notice, sender, allowlist.group_id, allowlist.topic_id)
        async with opener() as unit:
            await unit.outbox.record(
                result.update_id,
                result.status,
                result.external_message_id,
                result.attempt_count,
            )
            await unit.commit()
        logger.info("health update_id=%s status=%s", result.update_id, result.status)


async def iter_cycles(
    source: UpdateSource,
    opener: Opener,
    sender: HealthSender,
    allowlist: Allowlist,
    received_at: datetime,
    limit: int,
) -> AsyncIterator[list[IngestOutcome]]:
    for _ in range(limit):
        outcomes = await run_cycle(source, opener, sender, allowlist, received_at)
        yield outcomes
        if not outcomes:
            return
