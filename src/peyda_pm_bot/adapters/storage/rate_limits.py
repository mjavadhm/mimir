"""Durable quota deadlines survive a client or application restart."""

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import github_limits


class SqlLimits:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def get(self) -> tuple[float, float]:
        row = (await self.connection.execute(select(github_limits))).mappings().first()
        return (float(row["not_before"]), float(row["next_write"])) if row else (0, 0)

    async def save(self, not_before: float, next_write: float) -> None:
        await self.connection.execute(
            insert(github_limits)
            .values(id=1, not_before=not_before, next_write=next_write)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "not_before": func.max(github_limits.c.not_before, not_before),
                    "next_write": func.max(github_limits.c.next_write, next_write),
                },
            )
        )
