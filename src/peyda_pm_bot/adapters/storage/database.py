"""File-backed SQLite and short, serialized local units of work."""

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Self

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from peyda_pm_bot.adapters.storage.actions import SqlActions
from peyda_pm_bot.adapters.storage.memory import SqlMemory
from peyda_pm_bot.adapters.storage.queue import SqlQueue
from peyda_pm_bot.adapters.storage.rate_limits import SqlLimits
from peyda_pm_bot.adapters.storage.store import SqlStore
from peyda_pm_bot.adapters.storage.turns import SqlTurns
from peyda_pm_bot.adapters.storage.work import SqlWork


def migration_config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[4] / "migrations"))
    config.set_main_option(
        "sqlalchemy.url",
        URL.create("sqlite", database=str(path))
        .render_as_string(hide_password=False)
        .replace("%", "%%"),
    )
    return config


def migrate(path: Path) -> None:
    """Apply explicit migrations, never implicit create_all on startup."""
    command.upgrade(migration_config(path), "head")


class Database:
    def __init__(self, path: Path) -> None:
        self.engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(path)))

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure(connection: object, _: object) -> None:
            # SQLAlchemy's DBAPI event exposes an adapted connection, without a typed protocol.
            cursor = connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    def unit(self) -> "SqlUnitOfWork":
        return SqlUnitOfWork(self)

    async def close(self) -> None:
        await self.engine.dispose()

    async def initialize(self, path: Path, project_id: str) -> None:
        await asyncio.to_thread(migrate, path)
        async with self.unit() as unit:
            await unit.store.recover(project_id)


class SqlUnitOfWork:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.connection: AsyncConnection
        self.store: SqlStore

    async def __aenter__(self) -> Self:
        self.connection = await self.database.engine.connect()
        try:
            # Serialize local claims and checkpoint writes across connections.
            await self.connection.exec_driver_sql("BEGIN IMMEDIATE")
        except BaseException:
            await self.connection.close()
            raise
        self.store = SqlStore(self.connection)
        self.incoming = self.store
        self.outgoing = self.store
        self.queue = SqlQueue(self.connection)
        self.memory = SqlMemory(self.connection, self.incoming)
        self.turns = SqlTurns(self.connection)
        self.actions = SqlActions(self.connection)
        self.work = SqlWork(self.connection)
        self.github_limits = SqlLimits(self.connection)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        finish = asyncio.create_task(self._finish(commit=exc_type is None))
        try:
            await asyncio.shield(finish)
        except asyncio.CancelledError:
            # A supervisor must wait for the already-started transaction cleanup.
            await finish
            raise

    async def _finish(self, *, commit: bool) -> None:
        try:
            if commit:
                await self.connection.commit()
            else:
                await self.connection.rollback()
        finally:
            await self.connection.close()
