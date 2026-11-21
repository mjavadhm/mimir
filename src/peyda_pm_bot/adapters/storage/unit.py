"""Writers for one SQLite session. They do not commit."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from peyda_pm_bot.adapters.storage.models import (
    CheckpointRow,
    InboxRow,
    MessageVersionRow,
    OutboxRow,
)


class HealthRow:
    update_id: int
    attempt_count: int

    def __init__(self, update_id: int, attempt_count: int) -> None:
        self.update_id = update_id
        self.attempt_count = attempt_count


class StorageUnit:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.inbox = _Inbox(session)
        self.messages = _Messages(session)
        self.checkpoint = _Checkpoint(session)
        self.outbox = _Outbox(session)

    async def commit(self) -> None:
        await self.session.commit()

    async def counts(self) -> tuple[int, int, int, bool]:
        accepted = await self.session.scalar(
            select(func.count()).select_from(InboxRow).where(InboxRow.admission == "accepted")
        )
        ignored = await self.session.scalar(
            select(func.count()).select_from(InboxRow).where(InboxRow.admission == "ignored")
        )
        checkpoint = await self.session.scalar(select(CheckpointRow.next_offset))
        plain = await self.session.scalar(
            select(func.count())
            .select_from(MessageVersionRow)
            .where(
                MessageVersionRow.text != "",
                MessageVersionRow.text.notlike("/%"),
            )
        )
        return int(accepted or 0), int(ignored or 0), int(checkpoint or 0), int(plain or 0) > 0

    async def update_ids(self) -> list[int]:
        return list(await self.session.scalars(select(InboxRow.update_id)))


class _Inbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def contains(self, update_id: int) -> bool:
        found = await self._session.get(InboxRow, update_id)
        return found is not None

    async def add(
        self,
        update_id: int,
        kind: str,
        admission: str,
        reason: str | None,
        received_at: str,
    ) -> None:
        self._session.add(
            InboxRow(
                update_id=update_id,
                kind=kind,
                admission=admission,
                reason=reason,
                received_at=received_at,
            )
        )
        await self._session.flush()


class _Messages:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_version(self, chat_id: int, message_id: int) -> int:
        statement = select(MessageVersionRow.version).where(
            MessageVersionRow.chat_id == chat_id,
            MessageVersionRow.message_id == message_id,
        )
        versions = (await self._session.scalars(statement)).all()
        return max(versions, default=0) + 1

    async def add(
        self,
        update_id: int,
        chat_id: int,
        topic_id: int,
        message_id: int,
        sender_id: int,
        reply_to_message_id: int | None,
        text: str,
        message_at: str,
        edited_at: str | None,
        version: int,
    ) -> None:
        self._session.add(
            MessageVersionRow(
                update_id=update_id,
                chat_id=chat_id,
                topic_id=topic_id,
                message_id=message_id,
                sender_id=sender_id,
                reply_to_message_id=reply_to_message_id,
                text=text,
                message_at=message_at,
                edited_at=edited_at,
                version=version,
            )
        )
        await self._session.flush()


class _Checkpoint:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_offset(self) -> int:
        row = await self._session.get(CheckpointRow, 1)
        if row is None:
            raise RuntimeError("checkpoint row is missing")
        return row.next_offset

    async def advance_to(self, next_offset: int) -> None:
        row = await self._session.get(CheckpointRow, 1)
        if row is None:
            raise RuntimeError("checkpoint row is missing")
        if next_offset > row.next_offset:
            row.next_offset = next_offset
            await self._session.flush()


class _Outbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_health(self, update_id: int) -> None:
        self._session.add(
            OutboxRow(
                update_id=update_id,
                kind="health",
                status="pending",
                attempt_count=0,
                external_message_id=None,
            )
        )
        await self._session.flush()

    async def pending_health(self) -> Sequence[HealthRow]:
        statement = (
            select(OutboxRow)
            .where(OutboxRow.kind == "health", OutboxRow.status == "pending")
            .order_by(OutboxRow.id)
        )
        rows = (await self._session.scalars(statement)).all()
        return [HealthRow(row.update_id, row.attempt_count) for row in rows]

    async def record(
        self,
        update_id: int,
        status: str,
        external_message_id: int | None,
        attempt_count: int,
    ) -> None:
        statement = select(OutboxRow).where(OutboxRow.update_id == update_id)
        row = await self._session.scalar(statement)
        if row is None:
            raise RuntimeError(f"outbox row for update {update_id} is missing")
        row.status = status
        row.external_message_id = external_message_id
        row.attempt_count = attempt_count
        await self._session.flush()
