"""Queue SQL, always executed inside the caller's short unit of work."""

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as upsert
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import agent_state, job_messages, jobs, outbox, versions
from peyda_pm_bot.adapters.storage.store import message_codec
from peyda_pm_bot.domain.agent.models import (
    AgentRequest,
    AgentResult,
    SendMessage,
    SessionState,
    SetReaction,
)
from peyda_pm_bot.domain.agent.outputs import encode_result, validate_actions
from peyda_pm_bot.domain.conversations.events import IncomingUpdate


class SqlQueue:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def state(self) -> SessionState:
        row = (await self.connection.execute(select(agent_state))).mappings().one()
        return SessionState(
            row["session_id"],
            row["generation"],
            row["blocked"],
            row["turbo_requested"],
            row["turbo_effective"],
        )

    async def connected(self, session_id: str) -> None:
        await self.connection.execute(
            update(agent_state).values(
                session_id=session_id, generation=agent_state.c.generation + 1
            )
        )

    async def recover(self) -> None:
        active = await self.connection.scalar(select(jobs.c.id).where(jobs.c.status == "running"))
        if active is not None:
            await self.connection.execute(
                update(jobs).where(jobs.c.id == active).values(status="unknown")
            )
            await self.connection.execute(update(agent_state).values(blocked=True))

    async def add(self, event: IncomingUpdate, now: float, quiet: float, ceiling: float) -> None:
        message = event.message
        if message is None:
            return
        # Invalidate all snapshots that depended on the edited message.
        if message.is_edit:
            affected = select(job_messages.c.job_id).where(
                job_messages.c.message_id == message.message_id
            )
            await self.connection.execute(
                update(jobs)
                .where(jobs.c.id.in_(affected), jobs.c.status == "running")
                .values(status="superseded")
            )
            await self.connection.execute(
                update(outbox)
                .where(
                    outbox.c.job_id.in_(affected),
                    outbox.c.status == "pending",
                    outbox.c.purpose == "reply",
                )
                .values(status="failed")
            )
            pending = await self.connection.scalar(
                select(jobs.c.id)
                .where(jobs.c.id.in_(affected), jobs.c.status == "pending")
                .order_by(jobs.c.id)
                .limit(1)
            )
            if pending is not None:
                await self.connection.execute(
                    update(job_messages)
                    .where(
                        job_messages.c.job_id == pending,
                        job_messages.c.message_id == message.message_id,
                    )
                    .values(update_id=event.update_id)
                )
                await self.connection.execute(
                    update(jobs)
                    .where(jobs.c.id == pending)
                    .values(
                        revision=jobs.c.revision + 1,
                        due=func.min(now + quiet, jobs.c.first_at + ceiling),
                    )
                )
                return
        candidate = (
            (
                await self.connection.execute(
                    select(jobs)
                    .where(jobs.c.status == "pending", jobs.c.due > now)
                    .order_by(jobs.c.id.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        job_id = None
        if candidate:
            evidence = (
                (
                    await self.connection.execute(
                        select(versions.c.payload)
                        .join(job_messages, versions.c.update_id == job_messages.c.update_id)
                        .where(job_messages.c.job_id == candidate["id"])
                    )
                )
                .scalars()
                .all()
            )
            size = sum(len(message_codec.validate_json(item).text or "") for item in evidence)
            if len(evidence) < 50 and size + len(message.text or "") <= 32000:
                job_id = candidate["id"]
                await self.connection.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id)
                    .values(
                        due=min(now + quiet, candidate["first_at"] + ceiling),
                        revision=jobs.c.revision + 1,
                    )
                )
        if job_id is None:
            result = await self.connection.execute(
                insert(jobs).values(status="pending", first_at=now, due=now + min(quiet, ceiling))
            )
            assert result.inserted_primary_key is not None
            job_id = result.inserted_primary_key[0]
        await self.connection.execute(
            upsert(job_messages)
            .values(job_id=job_id, message_id=message.message_id, update_id=event.update_id)
            .on_conflict_do_update(
                index_elements=["job_id", "message_id"], set_={"update_id": event.update_id}
            )
        )

    async def claim(self, now: float) -> AgentRequest | None:
        state = await self.state()
        if state.blocked or await self.connection.scalar(
            select(jobs.c.id).where(jobs.c.status == "running")
        ):
            return None
        row = (
            (
                await self.connection.execute(
                    select(jobs)
                    .where(jobs.c.status == "pending", jobs.c.due <= now)
                    .order_by(jobs.c.due, jobs.c.id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        await self.connection.execute(
            update(jobs)
            .where(jobs.c.id == row["id"])
            .values(status="running", attempts=jobs.c.attempts + 1, generation=state.generation)
        )
        evidence = (
            (
                await self.connection.execute(
                    select(versions.c.update_id, versions.c.payload)
                    .join(job_messages, versions.c.update_id == job_messages.c.update_id)
                    .where(job_messages.c.job_id == row["id"])
                    .order_by(versions.c.update_id)
                )
            )
            .mappings()
            .all()
        )
        messages = tuple(
            IncomingUpdate(
                item["update_id"], "message", message_codec.validate_json(item["payload"])
            )
            for item in evidence
        )
        return AgentRequest(row["id"], row["revision"], state.generation, messages)

    async def wanted(self, request: AgentRequest) -> bool:
        row = (
            (await self.connection.execute(select(jobs).where(jobs.c.id == request.job_id)))
            .mappings()
            .one()
        )
        return bool(
            row["status"] == "running"
            and row["revision"] == request.revision
            and row["generation"] == request.generation
        )

    async def complete(self, request: AgentRequest, result: AgentResult) -> bool:
        if not await self.wanted(request):
            return False
        actions = validate_actions(request, result)
        messages = [item.message for item in request.messages if item.message is not None]
        destination = messages[-1]
        await self.connection.execute(
            update(jobs)
            .where(jobs.c.id == request.job_id)
            .values(status="succeeded", result=encode_result(result, actions))
        )
        predecessor = None
        for index, action in enumerate(actions):
            key = f"job:{request.job_id}:{request.revision}:{index}"
            if isinstance(action, SendMessage):
                for part, start in enumerate(range(0, len(action.text), 3500)):
                    inserted = await self.connection.execute(
                        insert(outbox).values(
                            delivery_key=f"{key}:{part}",
                            job_id=request.job_id,
                            chat_id=destination.chat_id,
                            topic_id=destination.topic_id,
                            reply_to_id=action.reply_to_id,
                            text=action.text[start : start + 3500],
                            status="pending",
                            depends_on=predecessor,
                        )
                    )
                    assert inserted.inserted_primary_key is not None
                    predecessor = inserted.inserted_primary_key[0]
            elif isinstance(action, SetReaction):
                # A newer desired reaction replaces a pending older one on the same message.
                await self.connection.execute(
                    update(outbox)
                    .where(
                        outbox.c.operation == "reaction",
                        outbox.c.chat_id == destination.chat_id,
                        outbox.c.target_id == action.message_id,
                        outbox.c.status == "pending",
                    )
                    .values(status="failed")
                )
                await self.connection.execute(
                    insert(outbox).values(
                        delivery_key=key,
                        job_id=request.job_id,
                        chat_id=destination.chat_id,
                        topic_id=destination.topic_id,
                        reply_to_id=None,
                        text="",
                        status="pending",
                        operation="reaction",
                        target_id=action.message_id,
                        reaction_emoji=action.emoji,
                    )
                )
        return True

    async def fail(self, request: AgentRequest, status: str) -> None:
        changed = await self.connection.scalar(
            update(jobs)
            .where(
                jobs.c.id == request.job_id,
                jobs.c.status == "running",
                jobs.c.revision == request.revision,
                jobs.c.generation == request.generation,
            )
            .values(status=status)
            .returning(jobs.c.id)
        )
        if changed is None:
            return
        if status == "unknown":
            await self.connection.execute(update(agent_state).values(blocked=True))
        destination = next(
            (item.message for item in reversed(request.messages) if item.message is not None),
            None,
        )
        if destination is not None and status in {"failed", "unknown"}:
            detail = "نتیجه نامشخص است" if status == "unknown" else "پاسخ معتبر تولید نشد"
            await self.connection.execute(
                upsert(outbox)
                .values(
                    delivery_key=f"job-failure:{request.job_id}:{request.revision}:{request.generation}",
                    job_id=request.job_id,
                    chat_id=destination.chat_id,
                    topic_id=destination.topic_id,
                    reply_to_id=destination.message_id,
                    purpose="job_failure",
                    status="pending",
                    text=(
                        f"نوبت {request.job_id}: {detail}. "
                        f"برای تلاش دوباره: /retry {request.job_id}"
                    ),
                )
                .on_conflict_do_nothing(index_elements=[outbox.c.delivery_key])
            )

    async def control(self, command: str, argument: str) -> str:
        state = await self.state()
        if command == "/status":
            count = await self.connection.scalar(
                select(func.count()).select_from(jobs).where(jobs.c.status == "pending")
            )
            active = await self.connection.scalar(
                select(jobs.c.id).where(jobs.c.status == "running")
            )
            return f"active={active}; queued={count}; blocked={state.blocked}"
        if command == "/turbo":
            if argument in {"on", "off"}:
                await self.connection.execute(
                    update(agent_state).values(turbo_requested=argument == "on")
                )
            requested = argument if argument in {"on", "off"} else state.turbo_requested
            return f"Turbo requested={requested}; effective={state.turbo_effective}"
        if command in {"/cancel", "/retry"} and argument.isdecimal():
            job_id = int(argument)
            status = await self.connection.scalar(select(jobs.c.status).where(jobs.c.id == job_id))
            if command == "/cancel" and status in {"pending", "running", "unknown"}:
                await self.connection.execute(
                    update(jobs).where(jobs.c.id == job_id).values(status="cancelled")
                )
                await self.connection.execute(
                    update(outbox)
                    .where(
                        outbox.c.job_id == job_id,
                        outbox.c.status == "pending",
                        outbox.c.purpose == "reply",
                    )
                    .values(status="failed")
                )
            elif command == "/retry" and status in {"failed", "unknown"}:
                await self.connection.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id)
                    .values(status="pending", revision=jobs.c.revision + 1)
                )
            else:
                return "Request not applicable"
            remaining = await self.connection.scalar(
                select(func.count()).select_from(jobs).where(jobs.c.status == "unknown")
            )
            await self.connection.execute(update(agent_state).values(blocked=bool(remaining)))
            return f"{command}: {job_id}"
        return "Invalid command"

    async def turbo_applied(self, enabled: bool) -> None:
        await self.connection.execute(update(agent_state).values(turbo_effective=enabled))

    async def card(self, text: str, now: float) -> None:
        queued = await self.connection.scalar(
            select(func.count()).select_from(jobs).where(jobs.c.status == "pending")
        )
        text = f"{text} | صف: {queued}"
        state = (await self.connection.execute(select(agent_state))).mappings().one()
        if state["card_text"] == text or now - state["card_at"] < 3:
            return
        # An unknown card creation must never cause duplicate replacement cards.
        existing = (
            (
                await self.connection.execute(
                    select(outbox)
                    .where(outbox.c.purpose == "card")
                    .order_by(outbox.c.id.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if existing and existing["status"] == "failed" and existing["target_id"] is None:
            return
        if existing and existing["status"] in {"sending", "unknown"}:
            return
        destination = (
            await self.connection.execute(
                select(versions.c.payload).order_by(versions.c.update_id.desc()).limit(1)
            )
        ).scalar()
        if destination is None:
            return
        message = message_codec.validate_json(destination)
        if existing and existing["status"] == "pending":
            await self.connection.execute(
                update(outbox).where(outbox.c.id == existing["id"]).values(text=text)
            )
        else:
            await self.connection.execute(
                insert(outbox).values(
                    delivery_key=f"card:{now}",
                    purpose="card",
                    target_id=state["card_id"],
                    chat_id=message.chat_id,
                    topic_id=message.topic_id,
                    reply_to_id=message.message_id,
                    text=text,
                    status="pending",
                )
            )
        await self.connection.execute(update(agent_state).values(card_text=text, card_at=now))
