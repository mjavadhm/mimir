"""Action drafts and claims. Network requests never occur in this adapter."""

import time
from dataclasses import replace
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import (
    action_attempts,
    action_versions,
    actions,
    github_scope,
    memory_turns,
)
from peyda_pm_bot.domain.actions.models import (
    ActionDraft,
    ActionRun,
    ActionSpec,
    RemoteComment,
    RemoteIssue,
)
from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.memory.models import MemoryTurn

codec = TypeAdapter(ActionDraft)
remote_codec = TypeAdapter(RemoteIssue | RemoteComment)


class SqlActions:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def visible(self, turn: MemoryTurn | None, action_id: str) -> ActionDraft:
        query = select(action_versions).where(action_versions.c.action_id == action_id)
        if turn:
            query = query.where(
                (action_versions.c.published.is_(True)) | (action_versions.c.turn_id == turn.id)
            )
        else:
            query = query.where(action_versions.c.published.is_(True))
        row = (
            (
                await self.connection.execute(
                    query.order_by(action_versions.c.revision.desc()).limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Action not found")
        return replace(
            codec.validate_json(row["payload"]),
            submitted=row["submitted"],
            published=row["published"],
        )

    async def prepare(
        self,
        turn: MemoryTurn,
        key: str,
        spec: ActionSpec,
        base: RemoteIssue | RemoteComment | None,
        action_id: str | None,
        expected_revision: int,
        repository: str,
    ) -> ActionDraft:
        await self.bind_repository(repository)
        if not key.strip() or len(key) > 120 or expected_revision < 0:
            raise ValueError("Invalid action key or revision")
        old = (
            (
                await self.connection.execute(
                    select(action_versions).where(
                        action_versions.c.turn_id == turn.id, action_versions.c.operation_key == key
                    )
                )
            )
            .mappings()
            .first()
        )
        if old:
            draft = codec.validate_json(old["payload"])
            if (
                draft.spec != spec
                or (action_id is not None and draft.id != action_id)
                or old["base_revision"] != expected_revision
            ):
                raise ValueError("Action key reused with different input")
            return replace(draft, submitted=old["submitted"], published=old["published"])
        count = await self.connection.scalar(
            select(func.count())
            .select_from(action_versions)
            .where(action_versions.c.turn_id == turn.id)
        )
        if count is not None and count >= 50:
            raise ValueError("Turn action-draft limit reached")
        previous = await self.visible(turn, action_id) if action_id else None
        if (previous.revision if previous else 0) != expected_revision:
            raise ValueError("Stale action revision")
        identity = action_id or str(uuid4())
        existing = await self.connection.scalar(
            select(actions.c.status).where(actions.c.id == identity)
        )
        if existing and existing not in {"draft", "stale", "failed"}:
            raise ValueError("Cancel or reconcile the queued action before editing it")
        latest = await self.connection.scalar(
            select(func.max(action_versions.c.revision)).where(
                action_versions.c.action_id == identity
            )
        )
        draft = ActionDraft(identity, int(latest or 0) + 1, spec, base, repository=repository)
        await self.connection.execute(
            insert(action_versions).values(
                action_id=identity,
                revision=draft.revision,
                turn_id=turn.id,
                operation_key=key,
                payload=codec.dump_json(draft).decode(),
                base_revision=expected_revision,
            )
        )
        return draft

    async def submit(self, turn: MemoryTurn, action_id: str, revision: int) -> ActionDraft:
        draft = await self.visible(turn, action_id)
        if draft.revision != revision:
            raise ValueError("Stale action submission")
        if draft.published:
            if (await self.current(action_id)).status != "draft":
                raise ValueError("Action is not an editable draft")
            draft = await self.prepare(
                turn,
                f"submit:{action_id}:{revision}",
                draft.spec,
                draft.base,
                action_id,
                revision,
                draft.repository,
            )
        await self.connection.execute(
            update(action_versions)
            .where(
                action_versions.c.action_id == action_id,
                action_versions.c.revision == draft.revision,
                action_versions.c.turn_id == turn.id,
            )
            .values(submitted=True)
        )
        return replace(draft, submitted=True)

    async def publish(self, request: AgentRequest) -> None:
        turns = select(memory_turns.c.id).where(
            memory_turns.c.job_id == request.job_id,
            memory_turns.c.request_revision == request.revision,
            memory_turns.c.generation == request.generation,
            memory_turns.c.state.in_(["open", "committed"]),
        )
        rows = (
            (
                await self.connection.execute(
                    select(action_versions)
                    .where(
                        action_versions.c.turn_id.in_(turns), action_versions.c.published.is_(False)
                    )
                    .order_by(action_versions.c.revision)
                )
            )
            .mappings()
            .all()
        )
        touched: set[str] = set()
        for row in rows:
            draft = codec.validate_json(row["payload"])
            current = (
                (await self.connection.execute(select(actions).where(actions.c.id == draft.id)))
                .mappings()
                .first()
            )
            if (current["revision"] if current else 0) != row["base_revision"]:
                raise ValueError("Action changed before publication")
            if (
                current
                and current["status"] not in {"draft", "stale", "failed"}
                and draft.id not in touched
            ):
                raise ValueError("Action became non-editable before publication")
            touched.add(draft.id)
            values = dict(
                revision=draft.revision,
                status="ready" if row["submitted"] else "draft",
                due=time.time(),
                cancel_requested=False,
                attempts=0,
                retry_from=0,
            )
            if current:
                await self.connection.execute(
                    update(actions).where(actions.c.id == draft.id).values(**values)
                )
            else:
                await self.connection.execute(insert(actions).values(id=draft.id, **values))
            await self.connection.execute(
                update(action_versions)
                .where(
                    action_versions.c.action_id == draft.id,
                    action_versions.c.revision == draft.revision,
                )
                .values(published=True)
            )

    async def current(self, action_id: str) -> ActionRun:
        row = (
            (await self.connection.execute(select(actions).where(actions.c.id == action_id)))
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Action not published")
        return ActionRun(
            await self.visible(None, action_id),
            row["status"],
            row["attempts"],
            row["cancel_requested"],
            row["retry_from"],
        )

    async def claim(self, now: float) -> ActionRun | None:
        if await self.connection.scalar(
            select(actions.c.id).where(actions.c.status == "executing")
        ):
            return None
        identity = await self.connection.scalar(
            select(actions.c.id)
            .where(actions.c.status.in_(["ready", "unknown"]), actions.c.due <= now)
            .order_by(actions.c.due, actions.c.id)
            .limit(1)
        )
        if identity is None:
            return None
        previous = await self.current(identity)
        await self.connection.execute(
            update(actions)
            .where(actions.c.id == identity)
            .values(status="executing", attempts=actions.c.attempts + 1)
        )
        await self.connection.execute(
            insert(action_attempts).values(
                action_id=identity,
                revision=previous.draft.revision,
                attempt=previous.attempts + 1,
                started_at=now,
                mode=previous.status,
            )
        )
        return replace(previous, attempts=previous.attempts + 1)

    async def result(
        self,
        action_id: str,
        status: str,
        remote: RemoteIssue | RemoteComment | None,
        detail: str = "",
        due: float = 0,
    ) -> None:
        await self.connection.execute(
            update(actions)
            .where(actions.c.id == action_id)
            .values(
                status=status,
                result=remote_codec.dump_json(remote).decode() if remote else None,
                detail=detail,
                due=due,
            )
        )

        current = await self.current(action_id)
        await self.connection.execute(
            update(action_attempts)
            .where(
                action_attempts.c.action_id == action_id,
                action_attempts.c.revision == current.draft.revision,
                action_attempts.c.attempt == current.attempts,
            )
            .values(
                finished_at=time.time(),
                status=status,
                detail=detail,
                result=remote_codec.dump_json(remote).decode() if remote else None,
            )
        )

    async def recover(self) -> None:
        await self.connection.execute(
            update(actions).where(actions.c.status == "executing").values(status="unknown", due=0)
        )

    async def cancel(self, action_id: str) -> str:
        run = await self.current(action_id)
        if run.status in {"draft", "ready", "stale", "failed"}:
            await self.result(action_id, "cancelled", None, "Cancelled before send")
            return "cancelled"
        if run.status in {"executing", "unknown"}:
            await self.connection.execute(
                update(actions).where(actions.c.id == action_id).values(cancel_requested=True)
            )
            return "reconciliation_required"
        return run.status

    async def retry(self, action_id: str) -> str:
        run = await self.current(action_id)
        if run.status == "unknown":
            await self.connection.execute(
                update(actions)
                .where(actions.c.id == action_id)
                .values(due=0, retry_from=run.attempts)
            )
            return "reconciliation_requested_no_resend"
        if run.status == "failed":
            await self.connection.execute(
                update(actions)
                .where(actions.c.id == action_id)
                .values(status="ready", due=0, retry_from=run.attempts)
            )
            return "ready"
        raise ValueError("Action cannot be retried")

    async def invalidate(self, source_ids: tuple[int, ...]) -> None:
        rows = (
            (
                await self.connection.execute(
                    select(actions).where(actions.c.status.in_(["draft", "ready"]))
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            run = await self.current(row["id"])
            if set(run.draft.spec.source_update_ids).intersection(source_ids):
                await self.result(row["id"], "stale", None, "Source message edited")

    async def bind_repository(self, repository: str) -> None:
        existing = await self.connection.scalar(select(github_scope.c.repository))
        if existing is None:
            await self.connection.execute(insert(github_scope).values(id=1, repository=repository))
        elif existing.casefold() != repository.casefold():
            raise ValueError(
                "Database is bound to another GitHub repository; use a separate database"
            )
