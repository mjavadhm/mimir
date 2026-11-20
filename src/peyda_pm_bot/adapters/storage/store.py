"""Concrete persistence operations; commit belongs to the unit of work."""

from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import (
    CARD_RETIRE,
    action_versions,
    agent_state,
    checkpoint,
    inbox,
    job_messages,
    memory_turns,
    outbox,
    versions,
)
from peyda_pm_bot.domain.actions.models import ActionRun, RemoteComment, RemoteIssue
from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.conversations.events import Delivery, IncomingUpdate, MessageEvent
from peyda_pm_bot.domain.memory.models import Evidence, MemoryRecord, normalize

message_codec = TypeAdapter(MessageEvent)


class SqlStore:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def offset(self) -> int:
        value = await self.connection.scalar(select(checkpoint.c.offset))
        return int(value or 0)

    async def ingest(self, event: IncomingUpdate, accepted: bool) -> None:
        payload = (
            message_codec.dump_json(event.message).decode()
            if accepted and event.message is not None
            else None
        )
        await self.connection.execute(
            sqlite_insert(inbox)
            .values(
                update_id=event.update_id,
                kind=event.kind,
                payload=payload,
                status="pending" if accepted else "ignored",
            )
            .on_conflict_do_nothing(index_elements=["update_id"])
        )

    async def advance(self, offset: int) -> None:
        await self.connection.execute(update(checkpoint).values(offset=offset))

    async def next_input(self) -> IncomingUpdate | None:
        row = (
            (
                await self.connection.execute(
                    select(inbox)
                    .where(inbox.c.status == "pending")
                    .order_by(inbox.c.update_id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return IncomingUpdate(
            row["update_id"], row["kind"], message_codec.validate_json(row["payload"])
        )

    async def finish_input(self, event: IncomingUpdate, response: str | None) -> None:
        message = event.message
        if message is None:
            raise ValueError("A pending input must have message evidence")
        await self.connection.execute(
            insert(versions).values(
                update_id=event.update_id,
                chat_id=message.chat_id,
                message_id=message.message_id,
                payload=message_codec.dump_json(message).decode(),
            )
        )
        if response is not None:
            await self.connection.execute(
                insert(outbox).values(
                    source_update_id=event.update_id,
                    chat_id=message.chat_id,
                    topic_id=message.topic_id,
                    reply_to_id=message.message_id,
                    text=response,
                    status="pending",
                )
            )
        await self.connection.execute(
            update(inbox).where(inbox.c.update_id == event.update_id).values(status="processed")
        )

    async def scheduled_notice(self, key: str, group: int, topic: int, text: str) -> None:
        await self.connection.execute(
            insert(outbox).values(
                delivery_key=key,
                purpose="scheduled",
                chat_id=group,
                topic_id=topic,
                text=text,
                status="pending",
            )
        )

    async def claim_delivery(self, now: datetime) -> Delivery | None:
        row = (
            (
                await self.connection.execute(
                    select(outbox)
                    .where(
                        outbox.c.status == "pending",
                        outbox.c.due <= now.timestamp(),
                        or_(
                            outbox.c.depends_on.is_(None),
                            outbox.c.depends_on.in_(
                                select(outbox.c.id).where(outbox.c.status == "sent")
                            ),
                        ),
                    )
                    .order_by(outbox.c.id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        attempts = int(row["attempts"]) + 1
        await self.connection.execute(
            update(outbox)
            .where(outbox.c.id == row["id"])
            .values(
                status="sending",
                attempts=attempts,
            )
        )
        return Delivery(
            row["id"],
            row["chat_id"],
            row["topic_id"],
            row["reply_to_id"],
            row["text"],
            attempts,
            row["target_id"],
            row["operation"],
            row["reaction_emoji"],
            rich_text=row["operation"] == "message"
            and (
                row["purpose"] == "card"
                or (row["purpose"] == "reply" and row["job_id"] is not None)
            ),
        )

    async def finish_delivery(
        self,
        delivery_id: int,
        status: str,
        external_id: int | None,
        due: datetime,
    ) -> None:
        row = (
            (
                await self.connection.execute(
                    select(
                        outbox.c.purpose,
                        outbox.c.operation,
                        outbox.c.target_id,
                        outbox.c.chat_id,
                        outbox.c.topic_id,
                    ).where(outbox.c.id == delivery_id)
                )
            )
            .mappings()
            .one()
        )
        state = (await self.connection.execute(select(agent_state))).mappings().one()
        created = (
            row["purpose"] == "card" and row["operation"] == "message" and row["target_id"] is None
        )
        retire = created and status == "sent" and state["card_text"] == CARD_RETIRE
        if retire and external_id is not None:
            await self.connection.execute(
                insert(outbox).values(
                    delivery_key=f"card-delete:{external_id}:{delivery_id}",
                    purpose="card",
                    operation="delete",
                    target_id=external_id,
                    chat_id=row["chat_id"],
                    topic_id=row["topic_id"],
                    text="",
                    status="pending",
                )
            )
            await self.connection.execute(
                update(agent_state).values(card_id=None, card_text="", card_at=0)
            )
        elif (
            row["purpose"] == "card"
            and row["operation"] == "message"
            and status == "sent"
            and (row["target_id"] is None or row["target_id"] == state["card_id"])
        ):
            await self.connection.execute(update(agent_state).values(card_id=external_id))
        elif created and status == "failed" and state["card_id"] is None:
            await self.connection.execute(update(agent_state).values(card_id=None, card_text=""))
        await self.connection.execute(
            update(outbox)
            .where(
                outbox.c.id == delivery_id,
                outbox.c.status == "sending",
            )
            .values(status=status, external_id=external_id, due=due.timestamp())
        )

    async def recover(self, project_id: str) -> None:
        existing = await self.connection.scalar(select(checkpoint.c.project_id))
        if existing is None:
            await self.connection.execute(
                insert(checkpoint).values(id=1, offset=0, project_id=project_id)
            )
        elif existing != project_id:
            raise ValueError("Database belongs to another project")
        # An interrupted network submission might already have reached Telegram.
        await self.connection.execute(
            update(outbox).where(outbox.c.status == "sending").values(status="unknown")
        )

    async def search_evidence(self, query: str) -> list[Evidence]:
        # Search preserves original message evidence; normalization is query-only.
        ids = []
        for row in (
            await self.connection.execute(select(versions).order_by(versions.c.update_id.desc()))
        ).mappings():
            message = message_codec.validate_json(row["payload"])
            if query in normalize(message.text or ""):
                ids.append(row["update_id"])
        evidence = await self.evidence(tuple(ids))
        return list(reversed(evidence))

    async def memory_notice(self, request: AgentRequest, records: list[MemoryRecord]) -> None:
        important = list({item.id: item for item in records if item.kind == "note"}.values())
        if not important:
            return
        message = request.messages[-1].message
        if message is None:
            raise ValueError("Missing notification destination")
        text = "یادداشت‌های مستند در حافظه ثبت شدند:\n" + "\n".join(
            f"{item.title} — {item.status} (v{item.revision}; {item.id})" for item in important
        )
        predecessor = None
        for part, start in enumerate(range(0, len(text), 3500)):
            result = await self.connection.execute(
                insert(outbox).values(
                    delivery_key=f"memory:{request.job_id}:{request.revision}:{part}",
                    job_id=request.job_id,
                    purpose="memory",
                    chat_id=message.chat_id,
                    topic_id=message.topic_id,
                    reply_to_id=message.message_id,
                    text=text[start : start + 3500],
                    status="pending",
                    depends_on=predecessor,
                )
            )
            assert result.inserted_primary_key is not None
            predecessor = result.inserted_primary_key[0]

    async def source_ids(self, chat_id: int, message_id: int) -> tuple[int, ...]:
        return tuple(
            (
                await self.connection.execute(
                    select(versions.c.update_id).where(
                        versions.c.chat_id == chat_id, versions.c.message_id == message_id
                    )
                )
            ).scalars()
        )

    async def evidence(self, update_ids: tuple[int, ...]) -> list[Evidence]:
        rows = (
            (
                await self.connection.execute(
                    select(versions)
                    .where(versions.c.update_id.in_(update_ids))
                    .order_by(versions.c.update_id)
                )
            )
            .mappings()
            .all()
        )
        result = []
        for row in rows:
            message = message_codec.validate_json(row["payload"])
            latest = await self.connection.scalar(
                select(func.max(versions.c.update_id)).where(
                    versions.c.chat_id == message.chat_id,
                    versions.c.message_id == message.message_id,
                )
            )
            result.append(
                Evidence(
                    row["update_id"],
                    message.message_id,
                    message.chat_id,
                    message.speaker_id,
                    message.text,
                    message.reply_to_id,
                    message.sent_at.isoformat(),
                    message.edited_at.isoformat() if message.edited_at else None,
                    latest != row["update_id"],
                    message.speaker_name,
                    message.sender_chat_id,
                    message.topic_id,
                )
            )
        return result

    async def action_notice(
        self, run: ActionRun, status: str, remote: RemoteIssue | RemoteComment | None, detail: str
    ) -> None:
        # Runtime-owned destination is derived from durable input, never agent-supplied URLs.
        turn_id = await self.connection.scalar(
            select(action_versions.c.turn_id).where(
                action_versions.c.action_id == run.draft.id,
                action_versions.c.revision == run.draft.revision,
            )
        )
        job_id = await self.connection.scalar(
            select(memory_turns.c.job_id).where(memory_turns.c.id == turn_id)
        )
        payload = await self.connection.scalar(
            select(versions.c.payload)
            .join(job_messages, versions.c.update_id == job_messages.c.update_id)
            .where(job_messages.c.job_id == job_id)
            .order_by(versions.c.update_id.desc())
            .limit(1)
        )
        if payload is None:
            raise ValueError("No action notification source")
        message = message_codec.validate_json(payload)
        text = f"GitHub action {run.draft.id}: {status}\n{detail}"
        if remote:
            text += "\n" + remote.url
        await self.connection.execute(
            sqlite_insert(outbox)
            .values(
                delivery_key=f"action:{run.draft.id}:{run.draft.revision}:{status}:{detail}",
                job_id=job_id,
                purpose="action",
                chat_id=message.chat_id,
                topic_id=message.topic_id,
                reply_to_id=message.message_id,
                text=text,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["delivery_key"])
        )
