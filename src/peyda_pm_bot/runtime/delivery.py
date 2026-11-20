"""Outbox delivery, with explicit safe-retry versus ambiguous outcomes."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from peyda_pm_bot.application.ports import (
    PermanentFailure,
    TemporaryFailure,
    Transport,
    UnknownOutcome,
    UowFactory,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Sender:
    def __init__(
        self,
        uow: UowFactory,
        transport: Transport,
        *,
        max_attempts: int = 3,
        retry_base: float = 1,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.uow = uow
        self.transport = transport
        self.max_attempts = max_attempts
        self.retry_base = retry_base
        self.clock = clock

    async def send_one(self) -> bool:
        async with self.uow() as unit:
            delivery = await unit.outgoing.claim_delivery(self.clock())
        if delivery is None:
            return False
        status, external_id, due = "unknown", None, self.clock()
        try:
            async with asyncio.timeout(30):
                external_id = await self.transport.send(delivery)
            status = "sent"
        except TemporaryFailure as error:
            status = "pending" if delivery.attempts < self.max_attempts else "failed"
            due += timedelta(
                seconds=max(
                    error.retry_after,
                    self.retry_base * 2 ** (delivery.attempts - 1),
                )
            )
        except PermanentFailure:
            status = "failed"
        except (UnknownOutcome, TimeoutError):
            status = "unknown"
        except asyncio.CancelledError:
            # The committed sending claim is recovered as unknown at the next startup.
            raise
        async with self.uow() as unit:
            await unit.outgoing.finish_delivery(delivery.id, status, external_id, due)
        logger.info("delivery result outbox_id=%s status=%s", delivery.id, status)
        return True
