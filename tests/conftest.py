import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from peyda_pm_bot.adapters.storage.database import Database
from peyda_pm_bot.application.conversations.service import ConversationPolicy, Conversations
from peyda_pm_bot.config import Settings, TelegramSettings
from peyda_pm_bot.domain.conversations.events import IncomingUpdate, MessageEvent


@pytest.fixture(autouse=True)
def isolate_live_credentials(monkeypatch):
    """Routine tests never inherit the developer's .env or live process credentials."""
    for key in os.environ:
        if key.startswith("PEYDA_"):
            monkeypatch.delenv(key)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(TelegramSettings.model_config, "env_file", None)


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    path = tmp_path / "test.db"
    database = Database(path)
    await database.initialize(path, "test")
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def conversations(database: Database) -> Conversations:
    return Conversations(database.unit, ConversationPolicy(-1001, 42, frozenset({7})))


def event(update_id: int = 10, **changes: object) -> IncomingUpdate:
    values = dict(
        chat_id=-1001,
        topic_id=42,
        message_id=100,
        speaker_id=7,
        speaker_name="Admin",
        sender_chat_id=None,
        reply_to_id=50,
        text="/health",
        sent_at=datetime(2026, 11, 17, tzinfo=UTC),
        edited_at=None,
        is_edit=False,
    )
    values.update(changes)
    # Construction intentionally accepts flexible malformed/scenario data in tests.
    return IncomingUpdate(
        update_id, "edited_message" if values["is_edit"] else "message", MessageEvent(**values)
    )
