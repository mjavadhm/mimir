"""Async SQLite engine. Connecting is explicit; importing this module does not open a file."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from peyda_pm_bot.adapters.storage.unit import StorageUnit


@runtime_checkable
class _SqlCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


@runtime_checkable
class _SqlConnection(Protocol):
    def cursor(self) -> _SqlCursor: ...


def database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve()}"


def create_engine(path: Path) -> AsyncEngine:
    engine = create_async_engine(database_url(path), connect_args={"timeout": 30})

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(connection: object, _record: object) -> None:
        # SQLAlchemy wraps aiosqlite in an adapter. The adapter's cursor is synchronous here.
        if not isinstance(connection, _SqlConnection):
            raise RuntimeError("sqlite connection has no cursor")
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def open_unit(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[StorageUnit]:
    """One session for one unit of work. The caller commits before leaving."""

    session = factory()
    unit = StorageUnit(session)
    try:
        yield unit
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
