"""Persist one batch, then let the caller commit. Nothing here talks to the network."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from peyda_pm_bot.application.conversations.ports import IngestStores
from peyda_pm_bot.domain.conversations.admission import admit, is_health_command
from peyda_pm_bot.domain.conversations.models import (
    Admission,
    Allowlist,
    IncomingUpdate,
)

_MAX_HEALTH_ATTEMPTS = 3


class IngestOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    update_id: int
    duplicate: bool
    admission: Admission | None = None
    health_pending: bool = False


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise ValueError("message timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat()


def _utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return moment.astimezone(UTC).isoformat()


async def ingest_batch(
    stores: IngestStores,
    updates: list[IncomingUpdate],
    allowlist: Allowlist,
    received_at: datetime,
) -> list[IngestOutcome]:
    """Record updates and move the checkpoint in the caller's open transaction."""

    ordered = sorted(updates, key=lambda item: item.update_id)
    outcomes: list[IngestOutcome] = []
    next_offset = await stores.checkpoint.current_offset()
    for update in ordered:
        outcome = await _ingest_one(stores, update, allowlist, received_at)
        outcomes.append(outcome)
        candidate = update.update_id + 1
        if candidate > next_offset:
            next_offset = candidate
    if ordered:
        await stores.checkpoint.advance_to(next_offset)
    return outcomes


async def _ingest_one(
    stores: IngestStores,
    update: IncomingUpdate,
    allowlist: Allowlist,
    received_at: datetime,
) -> IngestOutcome:
    if await stores.inbox.contains(update.update_id):
        return IngestOutcome(update_id=update.update_id, duplicate=True)
    decision = admit(update, allowlist)
    await stores.inbox.add(
        update_id=update.update_id,
        kind=update.kind.value,
        admission=decision.admission.value,
        reason=decision.reason,
        received_at=_utc_iso(received_at),
    )
    health_pending = False
    if decision.admission is Admission.ACCEPTED:
        assert update.chat_id is not None
        assert update.message_id is not None
        assert update.topic_id is not None
        assert update.sender_id is not None
        version = await stores.messages.next_version(update.chat_id, update.message_id)
        await stores.messages.add(
            update_id=update.update_id,
            chat_id=update.chat_id,
            topic_id=update.topic_id,
            message_id=update.message_id,
            sender_id=update.sender_id,
            reply_to_message_id=update.reply_to_message_id,
            text=update.text or "",
            message_at=_iso(update.message_at) or _utc_iso(received_at),
            edited_at=_iso(update.edited_at),
            version=version,
        )
        if is_health_command(update, allowlist):
            await stores.outbox.add_health(update.update_id)
            health_pending = True
    return IngestOutcome(
        update_id=update.update_id,
        duplicate=False,
        admission=decision.admission,
        health_pending=health_pending,
    )


def health_attempt_cap() -> int:
    return _MAX_HEALTH_ATTEMPTS
