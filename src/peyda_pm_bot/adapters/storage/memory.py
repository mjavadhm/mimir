"""Memory persistence. Every method uses the caller's existing transaction."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import memory_revisions, memory_stages, memory_turns
from peyda_pm_bot.adapters.storage.turns import SqlTurns
from peyda_pm_bot.application.conversations.evidence import EvidenceReader
from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.memory.models import (
    Evidence,
    MemoryPolicy,
    MemoryRecord,
    MemoryTurn,
    MemoryView,
    validate_record,
)

codec = TypeAdapter(MemoryRecord)


class SqlMemory:
    def __init__(self, connection: AsyncConnection, reader: EvidenceReader) -> None:
        self.connection = connection
        self.reader = reader

    async def begin(self, request: AgentRequest, policy: MemoryPolicy, project_id: str) -> str:
        return await SqlTurns(self.connection).begin(request, policy, project_id)

    async def authorize(self, project_id: str, token: str) -> MemoryTurn:
        return await SqlTurns(self.connection).authorize(project_id, token)

    async def views(self, turn: MemoryTurn | None = None) -> list[MemoryView]:
        rows = (
            (
                await self.connection.execute(
                    select(memory_revisions).order_by(
                        memory_revisions.c.record_id, memory_revisions.c.revision
                    )
                )
            )
            .mappings()
            .all()
        )
        current: dict[str, MemoryView] = {}
        for row in rows:
            current[row["record_id"]] = MemoryView(
                codec.validate_json(row["payload"]),
                row["needs_review"],
                updated_at=row["created_at"],
            )
        if turn is not None:
            staged = (
                (
                    await self.connection.execute(
                        select(memory_stages)
                        .where(memory_stages.c.turn_id == turn.id)
                        .order_by(memory_stages.c.id)
                    )
                )
                .mappings()
                .all()
            )
            for row in staged:
                current[row["record_id"]] = MemoryView(
                    codec.validate_json(row["payload"]),
                    any(
                        item.superseded
                        for item in await self.evidence(
                            codec.validate_json(row["payload"]).source_update_ids
                        )
                    ),
                    True,
                    row["created_at"],
                )
        return list(current.values())

    async def history(self, record_id: str) -> list[MemoryView]:
        rows = (
            (
                await self.connection.execute(
                    select(memory_revisions)
                    .where(memory_revisions.c.record_id == record_id)
                    .order_by(memory_revisions.c.revision.desc())
                )
            )
            .mappings()
            .all()
        )
        return [
            MemoryView(
                codec.validate_json(row["payload"]),
                row["needs_review"],
                updated_at=row["created_at"],
                superseded=row["revision"] != rows[0]["revision"],
            )
            for row in rows
        ]

    async def evidence(self, update_ids: tuple[int, ...]) -> list[Evidence]:
        return await self.reader.evidence(update_ids)

    async def _validate(self, record: MemoryRecord, current: dict[str, MemoryView]) -> None:
        validate_record(record)
        evidence = await self.evidence(record.source_update_ids)
        if {item.update_id for item in evidence} != set(record.source_update_ids):
            raise ValueError("Unknown source version")
        speakers = {
            item.speaker_id
            for item in evidence
            if item.speaker_id is not None and item.sender_chat_id is None
        }
        if not set(record.attributed_user_ids).issubset(speakers):
            raise ValueError("Attribution must be backed by a cited speaker")
        for case_id in record.case_ids:
            case = current.get(case_id)
            if case is None or case.record.kind != "case" or case.record.status != "active":
                raise ValueError("Unknown or inactive case")
        if record.kind == "link":
            source = evidence[0]
            identity = f"link:{source.chat_id}:{source.message_id}"
            if record.id != identity:
                raise ValueError("Link identity must match its source message")

    async def stage(
        self, turn: MemoryTurn, key: str, record: MemoryRecord, expected_revision: int
    ) -> MemoryView:
        if not key.strip() or len(key) > 120 or expected_revision < 0:
            raise ValueError("Invalid operation key or revision")
        input_value = json.dumps(
            {"record": json.loads(codec.dump_json(record)), "expected": expected_revision},
            sort_keys=True,
            ensure_ascii=False,
        )
        old = (
            (
                await self.connection.execute(
                    select(memory_stages).where(
                        memory_stages.c.turn_id == turn.id, memory_stages.c.operation_key == key
                    )
                )
            )
            .mappings()
            .first()
        )
        if old:
            if old["input"] != input_value:
                raise ValueError("Operation key reused with different input")
            return MemoryView(codec.validate_json(old["payload"]), False, True, old["created_at"])
        count = await self.connection.scalar(
            select(func.count())
            .select_from(memory_stages)
            .where(memory_stages.c.turn_id == turn.id)
        )
        if count is not None and count >= 50:
            raise ValueError("Turn change limit reached")
        current = {view.record.id: view for view in await self.views(turn)}
        identity = record.id
        if record.kind == "link":
            evidence = await self.evidence(record.source_update_ids)
            if len(evidence) != 1:
                raise ValueError("Link requires one source")
            source = evidence[0]
            identity = f"link:{source.chat_id}:{source.message_id}"
        identity = identity or str(uuid4())
        previous = current.get(identity)
        if (previous.record.revision if previous else 0) != expected_revision:
            raise ValueError("Stale memory revision")
        if previous and previous.record.kind != record.kind:
            raise ValueError("Cannot change record kind")
        if (
            record.kind == "note"
            and previous
            and previous.record.category == "decision"
            and not record.reason.strip()
        ):
            raise ValueError("Decision changes require a reason")
        prepared = replace(record, id=identity, revision=expected_revision + 1)
        await self._validate(prepared, current)
        created_at = datetime.now(UTC).isoformat()
        await self.connection.execute(
            insert(memory_stages).values(
                turn_id=turn.id,
                operation_key=key,
                input=input_value,
                created_at=created_at,
                record_id=identity,
                expected_revision=expected_revision,
                payload=codec.dump_json(prepared).decode(),
            )
        )
        return MemoryView(
            prepared,
            any(item.superseded for item in await self.evidence(prepared.source_update_ids)),
            True,
            created_at,
        )

    async def commit(self, request: AgentRequest) -> list[MemoryRecord]:
        turns = (
            (
                await self.connection.execute(
                    select(memory_turns.c.id).where(
                        memory_turns.c.job_id == request.job_id,
                        memory_turns.c.request_revision == request.revision,
                        memory_turns.c.generation == request.generation,
                        memory_turns.c.state == "open",
                    )
                )
            )
            .scalars()
            .all()
        )
        if not turns:
            return []
        if len(turns) != 1:
            raise ValueError("Ambiguous memory turn")
        stages = (
            (
                await self.connection.execute(
                    select(memory_stages)
                    .where(memory_stages.c.turn_id == turns[0])
                    .order_by(memory_stages.c.id)
                )
            )
            .mappings()
            .all()
        )
        current = {view.record.id: view for view in await self.views()}
        changed = []
        for row in stages:
            record = codec.validate_json(row["payload"])
            previous = current.get(record.id)
            if (previous.record.revision if previous else 0) != row["expected_revision"]:
                raise ValueError("Stale memory commit")
            await self._validate(record, current)
            evidence = await self.evidence(record.source_update_ids)
            needs_review = any(item.superseded for item in evidence)
            await self.connection.execute(
                insert(memory_revisions).values(
                    record_id=record.id,
                    revision=record.revision,
                    turn_id=turns[0],
                    payload=row["payload"],
                    needs_review=needs_review,
                    created_at=row["created_at"],
                )
            )
            # A corrected link or changed decision makes existing case summaries stale.
            affected = set(record.case_ids)
            if previous:
                affected.update(previous.record.case_ids)
            if record.kind in {"link", "note"}:
                for view in current.values():
                    if view.record.kind == "summary" and affected.intersection(
                        view.record.case_ids
                    ):
                        await self.connection.execute(
                            update(memory_revisions)
                            .where(
                                memory_revisions.c.record_id == view.record.id,
                                memory_revisions.c.revision == view.record.revision,
                            )
                            .values(needs_review=True)
                        )
            current[record.id] = MemoryView(record, needs_review, updated_at=row["created_at"])
            changed.append(record)
        await self.connection.execute(
            update(memory_turns).where(memory_turns.c.id == turns[0]).values(state="committed")
        )
        return changed

    async def invalidate(self, chat_id: int, message_id: int) -> None:
        sources = set(await self.reader.source_ids(chat_id, message_id))
        rows = (await self.connection.execute(select(memory_revisions))).mappings().all()
        affected_cases: set[str] = set()
        for row in rows:
            record = codec.validate_json(row["payload"])
            if sources.intersection(record.source_update_ids):
                affected_cases.update(record.case_ids)
                if record.kind == "case":
                    affected_cases.add(record.id)
                await self.connection.execute(
                    update(memory_revisions)
                    .where(
                        memory_revisions.c.record_id == record.id,
                        memory_revisions.c.revision == record.revision,
                    )
                    .values(needs_review=True)
                )
        for row in rows:
            record = codec.validate_json(row["payload"])
            if record.kind == "summary" and affected_cases.intersection(record.case_ids):
                await self.connection.execute(
                    update(memory_revisions)
                    .where(
                        memory_revisions.c.record_id == record.id,
                        memory_revisions.c.revision == record.revision,
                    )
                    .values(needs_review=True)
                )
