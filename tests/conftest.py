"""Fixtures for offline ingest tests. These tests never read `.env`."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from peyda_pm_bot.adapters.storage.engine import create_engine, session_factory
from peyda_pm_bot.bootstrap import migrate
from peyda_pm_bot.domain.conversations.models import Allowlist, IncomingUpdate, UpdateKind

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
Database = tuple[Path, async_sessionmaker[AsyncSession]]


def incoming(**overrides: object) -> IncomingUpdate:
    fields: dict[str, object] = {
        "update_id": 1,
        "kind": UpdateKind.MESSAGE,
        "chat_id": -100,
        "topic_id": 7,
        "message_id": 10,
        "sender_id": 5,
        "text": "hello",
        "message_at": NOW,
    }
    fields.update(overrides)
    return IncomingUpdate.model_validate(fields)


@pytest.fixture
def allowlist() -> Allowlist:
    return Allowlist(group_id=-100, topic_id=7, admin_ids=frozenset({5}))


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    path = tmp_path / "phase-one.db"
    migrate(path)
    engine = create_engine(path)
    factory = session_factory(engine)
    try:
        yield path, factory
    finally:
        await engine.dispose()
