"""Durable inbox, checkpoint, edits, and restart."""

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from peyda_pm_bot.adapters.storage.engine import create_engine, open_unit, session_factory
from peyda_pm_bot.adapters.storage.models import InboxRow, MessageVersionRow, OutboxRow
from peyda_pm_bot.application.conversations.ingest import ingest_batch
from peyda_pm_bot.bootstrap import migrate
from peyda_pm_bot.domain.conversations.models import Allowlist, UpdateKind
from tests.conftest import NOW, Database, incoming


async def _rows(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[
    list[tuple[int, str | None]],
    list[tuple[int, str, int, int | None, str | None, int]],
    list[tuple[int, str, str]],
    int,
]:
    async with open_unit(factory) as unit:
        inbox = list(await unit.session.scalars(select(InboxRow).order_by(InboxRow.update_id)))
        versions = list(
            await unit.session.scalars(
                select(MessageVersionRow).order_by(MessageVersionRow.update_id)
            )
        )
        outbox = list(await unit.session.scalars(select(OutboxRow).order_by(OutboxRow.update_id)))
        offset = await unit.checkpoint.current_offset()
        copied_inbox = [(row.update_id, row.reason) for row in inbox]
        copied_versions = [
            (
                row.version,
                row.text,
                row.sender_id,
                row.reply_to_message_id,
                row.edited_at,
                row.message_id,
            )
            for row in versions
        ]
        copied_outbox = [(row.update_id, row.status, row.kind) for row in outbox]
    return copied_inbox, copied_versions, copied_outbox, offset


async def test_uncommitted_ingest_rolls_back(database: Database, allowlist: Allowlist) -> None:
    _path, factory = database
    async with open_unit(factory) as unit:
        await ingest_batch(unit, [incoming(text="not-yet")], allowlist, NOW)
    inbox, versions, _outbox, offset = await _rows(factory)
    assert inbox == []
    assert versions == []
    assert offset == 0


async def test_failed_message_write_rolls_back_inbox_and_offset(
    database: Database, allowlist: Allowlist
) -> None:
    _path, factory = database

    async with open_unit(factory) as unit:

        async def explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("write failed")

        unit.messages.add = explode  # type: ignore[method-assign]
        try:
            await ingest_batch(unit, [incoming(text="half-written")], allowlist, NOW)
        except RuntimeError as exc:
            assert str(exc) == "write failed"
        else:
            raise AssertionError("write should fail")

    inbox, versions, _outbox, offset = await _rows(factory)
    assert inbox == []
    assert versions == []
    assert offset == 0


async def test_commit_stores_identity_and_advances_offset_once(
    database: Database, allowlist: Allowlist
) -> None:
    _path, factory = database
    first = incoming(text="hello", reply_to_message_id=4)
    duplicate = incoming(update_id=1, text="hello again")
    later = incoming(update_id=3, message_id=11, text="next")
    async with open_unit(factory) as unit:
        outcomes = await ingest_batch(unit, [later, first, duplicate], allowlist, NOW)
        await unit.commit()
    assert [item.duplicate for item in outcomes] == [False, True, False]
    inbox, versions, _outbox, offset = await _rows(factory)
    assert [row[0] for row in inbox] == [1, 3]
    assert [item[1] for item in versions] == ["hello", "next"]
    assert versions[0][3] == 4
    assert offset == 4


async def test_edit_is_a_new_version_of_the_same_message(
    database: Database, allowlist: Allowlist
) -> None:
    _path, factory = database
    edited_at = datetime(2026, 11, 21, 12, 5, tzinfo=NOW.tzinfo)
    async with open_unit(factory) as unit:
        await ingest_batch(
            unit,
            [
                incoming(update_id=1, text="first"),
                incoming(
                    update_id=2,
                    kind=UpdateKind.EDITED_MESSAGE,
                    text="second",
                    edited_at=edited_at,
                ),
            ],
            allowlist,
            NOW,
        )
        await unit.commit()
    _inbox, versions, _outbox, offset = await _rows(factory)
    assert [item[0] for item in versions] == [1, 2]
    assert [item[1] for item in versions] == ["first", "second"]
    assert versions[0][4] is None
    assert versions[1][4] == edited_at.isoformat()
    assert {item[5] for item in versions} == {10}
    assert offset == 3


async def test_out_of_scope_text_is_absent_from_sqlite(
    database: Database, allowlist: Allowlist
) -> None:
    path, factory = database
    secret = "secret-phrase-not-stored"
    async with open_unit(factory) as unit:
        outcome = (
            await ingest_batch(
                unit,
                [incoming(update_id=8, chat_id=-200, text=secret)],
                allowlist,
                NOW,
            )
        )[0]
        await unit.commit()
    assert outcome.admission is not None
    assert outcome.admission.value == "ignored"
    assert outcome.health_pending is False
    inbox, versions, _outbox, offset = await _rows(factory)
    assert inbox[0][1] == "outside_chat"
    assert versions == []
    assert offset == 9
    stored = _database_bytes(path)
    assert secret.encode() not in stored


async def test_restart_does_not_apply_a_stored_update_again(
    tmp_path: Path, allowlist: Allowlist
) -> None:
    path = tmp_path / "restart.db"
    migrate(path)
    first = create_engine(path)
    async with open_unit(session_factory(first)) as unit:
        await ingest_batch(unit, [incoming(text="kept")], allowlist, NOW)
        await unit.commit()
    await first.dispose()

    second = create_engine(path)
    factory = session_factory(second)
    async with open_unit(factory) as unit:
        outcomes = await ingest_batch(unit, [incoming(text="kept")], allowlist, NOW)
        await unit.commit()
    assert outcomes[0].duplicate is True
    _inbox, versions, _outbox, offset = await _rows(factory)
    await second.dispose()
    assert versions == [(1, "kept", 5, None, None, 10)]
    assert offset == 2


async def test_health_is_queued_only_for_an_admin_command(
    database: Database, allowlist: Allowlist
) -> None:
    _path, factory = database
    async with open_unit(factory) as unit:
        outcomes = await ingest_batch(
            unit,
            [
                incoming(update_id=1, text="/health"),
                incoming(update_id=2, sender_id=8, message_id=11, text="/health"),
            ],
            allowlist,
            NOW,
        )
        await unit.commit()
    assert [item.health_pending for item in outcomes] == [True, False]
    _inbox, _versions, outbox, _offset = await _rows(factory)
    assert outbox == [(1, "pending", "health")]


async def test_naive_message_time_is_rejected(database: Database, allowlist: Allowlist) -> None:
    _path, factory = database
    async with open_unit(factory) as unit:
        try:
            await ingest_batch(
                unit,
                [incoming(message_at=datetime(2026, 11, 21, 12, 0))],
                allowlist,
                NOW,
            )
        except ValueError as exc:
            assert "timezone-aware" in str(exc)
        else:
            raise AssertionError("naive timestamps must fail")
    inbox, _versions, _outbox, offset = await _rows(factory)
    assert inbox == []
    assert offset == 0


def _database_bytes(path: Path) -> bytes:
    blob = path.read_bytes()
    for suffix in ("-wal", "-shm"):
        extra = path.with_name(path.name + suffix)
        if extra.is_file():
            blob += extra.read_bytes()
    return blob


async def test_sqlite_uses_wal_and_foreign_keys(database: Database) -> None:
    from sqlalchemy import text

    _path, factory = database
    async with factory() as session:
        mode = await session.scalar(text("PRAGMA journal_mode"))
        foreign_keys = await session.scalar(text("PRAGMA foreign_keys"))
    assert mode == "wal"
    assert foreign_keys == 1
