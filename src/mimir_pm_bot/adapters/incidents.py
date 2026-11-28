"""Operational error reports, kept separate from project conversations."""

import asyncio
import logging
import sqlite3
import time

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InputRichBlockParagraph, InputRichMessage, RichTextBold

from mimir_pm_bot.adapters.turn_store import Turns

log = logging.getLogger(__name__)


def report(component: str, reason: str, reference: str) -> InputRichMessage:
    return InputRichMessage(
        blocks=[
            InputRichBlockParagraph(text=RichTextBold(text=f"Mimir · {component}")),
            InputRichBlockParagraph(text=reason),
            InputRichBlockParagraph(text=reference),
        ]
    )


def record_exception(turns: Turns, component: str, exc: Exception, pile: int | None) -> None:
    with turns.transaction() as db:
        db.execute(
            "INSERT INTO incidents(component, reason, pile) VALUES(?,?,?)",
            (component, type(exc).__name__, pile),
        )


class IncidentChannel:
    def __init__(self, turns: Turns, bot: Bot, channel: int) -> None:
        self.turns, self.bot, self.channel = turns, bot, channel

    def claim(self) -> sqlite3.Row | None:
        with self.turns.transaction() as db:
            row: sqlite3.Row | None = db.execute(
                "SELECT * FROM incidents WHERE state='pending' AND due<=? ORDER BY id LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row:
                db.execute("UPDATE incidents SET state='sending' WHERE id=?", (row["id"],))
            return row

    def settle(self, ident: int, state: str, due: float) -> None:
        with self.turns.transaction() as db:
            db.execute("UPDATE incidents SET state=?,due=? WHERE id=?", (state, due, ident))

    async def deliver_one(self) -> None:
        row = await asyncio.to_thread(self.claim)
        if not row:
            return
        ref = f"Incident #{row['id']} · {row['occurred']} UTC"
        if row["pile"] is not None:
            ref += f" · Turn {row['pile']}"
        state, due = "sent", 0.0
        try:
            await self.bot.send_rich_message(
                self.channel, report(row["component"], row["reason"], ref)
            )
        except TelegramRetryAfter as exc:
            state, due = "pending", time.time() + exc.retry_after
        except (TelegramBadRequest, TelegramForbiddenError):
            state = "failed"
        except (TelegramAPIError, TimeoutError, OSError):
            state = "unknown"
        if state != "sent":
            log.warning("Incident %d channel delivery: %s", row["id"], state)
        await asyncio.to_thread(self.settle, row["id"], state, due)

    async def run(self) -> None:
        while True:
            await self.deliver_one()
            await asyncio.sleep(2)
