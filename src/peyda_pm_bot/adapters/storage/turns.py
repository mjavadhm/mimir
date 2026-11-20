"""Shared turn-grant storage; historical memory_turns table name is retained."""

import hashlib
import secrets

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import memory_turns
from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.memory.models import MemoryPolicy, MemoryTurn


class SqlTurns:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def begin(self, request: AgentRequest, policy: MemoryPolicy, project_id: str) -> str:
        # An earlier grant cannot authorize a retry of the same job.
        await self.connection.execute(
            update(memory_turns)
            .where(memory_turns.c.job_id == request.job_id, memory_turns.c.state == "open")
            .values(state="abandoned")
        )
        token = secrets.token_urlsafe(32)
        await self.connection.execute(
            insert(memory_turns).values(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                project_id=project_id,
                job_id=request.job_id,
                request_revision=request.revision,
                generation=request.generation,
                policy_version=policy.version,
                policy_text=policy.text,
                state="open",
            )
        )
        return token

    async def authorize(self, project_id: str, token: str) -> MemoryTurn:
        row = (
            (
                await self.connection.execute(
                    select(memory_turns).where(
                        memory_turns.c.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                        memory_turns.c.project_id == project_id,
                        memory_turns.c.state == "open",
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Invalid or closed memory turn")
        return MemoryTurn(row["id"], row["job_id"], row["request_revision"], row["generation"])
