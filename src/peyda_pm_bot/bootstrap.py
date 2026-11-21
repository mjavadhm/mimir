"""Wire the database, migration, and Telegram client. Importing this module connects to nothing."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from peyda_pm_bot.adapters.storage.engine import create_engine, open_unit, session_factory
from peyda_pm_bot.adapters.telegram.client import AiogramTelegram
from peyda_pm_bot.application.conversations.ingest import ingest_batch
from peyda_pm_bot.application.conversations.ports import IngestStores
from peyda_pm_bot.config import Settings
from peyda_pm_bot.domain.conversations.models import IncomingUpdate, UpdateKind
from peyda_pm_bot.runtime.cycle import Opener

_ROOT = Path(__file__).resolve().parents[2]


def alembic_ini() -> Path:
    candidate = _ROOT / "alembic.ini"
    if candidate.is_file():
        return candidate
    cwd_candidate = Path.cwd() / "alembic.ini"
    if cwd_candidate.is_file():
        return cwd_candidate
    raise FileNotFoundError("alembic.ini")


def migrate(database_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    ini = alembic_ini()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(ini))
    config.set_main_option("script_location", str(ini.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", "sqlite:///" + database_path.resolve().as_posix())
    command.upgrade(config, "head")


def make_telegram(token: str) -> AiogramTelegram:
    from aiogram import Bot

    return AiogramTelegram(Bot(token=token))


class App:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        factory: async_sessionmaker[AsyncSession],
        telegram: AiogramTelegram,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.telegram = telegram
        self._factory = factory

    def opener(self) -> Opener:
        factory = self._factory

        def open_stores() -> AbstractAsyncContextManager[IngestStores]:
            return _stores(factory)

        return open_stores

    async def close(self) -> None:
        await self.telegram.close()
        await self.engine.dispose()

    async def snapshot(self) -> tuple[int, int, int, bool]:
        async with open_unit(self._factory) as unit:
            return await unit.counts()

    async def replay_duplicates(self, received_at: datetime) -> bool:
        async with open_unit(self._factory) as unit:
            update_ids = await unit.update_ids()
        if not update_ids:
            return True
        duplicates = [
            IncomingUpdate(update_id=update_id, kind=UpdateKind.OTHER) for update_id in update_ids
        ]
        async with open_unit(self._factory) as unit:
            outcomes = await ingest_batch(unit, duplicates, self.settings.allowlist(), received_at)
            await unit.commit()
        return all(outcome.duplicate for outcome in outcomes)


def build(settings: Settings) -> App:
    migrate(settings.database_path)
    engine = create_engine(settings.database_path)
    return App(settings, engine, session_factory(engine), make_telegram(settings.bot_token))


@asynccontextmanager
async def _stores(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[IngestStores]:
    async with open_unit(factory) as unit:
        yield unit
