"""One durable progress message per dispatched pile, edited on discrete activities."""

import asyncio
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


class TurnCards:
    def __init__(self, turns: Turns, bot: Bot, group: int, topic: int) -> None:
        self.turns, self.bot, self.group, self.topic = turns, bot, group, topic

    def activity(self, pile: int, label: str) -> None:
        with self.turns.transaction() as db:
            db.execute(
                """UPDATE turn_cards SET activity=?, steps=steps+1,revision=revision+1
                WHERE pile=? AND EXISTS(SELECT 1 FROM piles WHERE id=? AND state='active')""",
                (label, pile, pile),
            )

    def claim(self) -> sqlite3.Row | None:
        with self.turns.transaction() as db:
            row: sqlite3.Row | None = db.execute(
                """SELECT c.*, p.state AS turn_state,p.chat,p.topic
                FROM turn_cards c JOIN piles p ON p.id=c.pile
                WHERE c.state='ready' AND (c.delivered<c.revision OR p.state!='active')
                AND c.due<=?
                ORDER BY c.pile LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if row:
                db.execute("UPDATE turn_cards SET state='sending' WHERE pile=?", (row["pile"],))
            return row

    def settle(
        self, row: sqlite3.Row, state: str, message: int | None, delay: float, success: bool
    ) -> None:
        with self.turns.transaction() as db:
            db.execute(
                """UPDATE turn_cards SET state=?,message=?,due=?,delivered=?,failures=?
                WHERE pile=?""",
                (
                    state,
                    message,
                    time.time() + delay,
                    row["revision"] if success else row["delivered"],
                    0 if success else row["failures"] + 1,
                    row["pile"],
                ),
            )

    async def deliver_one(self) -> None:
        row = await asyncio.to_thread(self.claim)
        if row is None:
            return
        finished = row["turn_state"] != "active"
        card = InputRichMessage(
            blocks=[
                InputRichBlockParagraph(text=RichTextBold(text="Antigravity")),
                InputRichBlockParagraph(text=row["activity"] or "Processing your request…"),
            ]
        )
        message = row["message"]
        state, delay, success = "ready", 3.0, True
        try:
            if finished:
                if message is not None:
                    await self.bot.delete_message(chat_id=row["chat"], message_id=message)
                state = "deleted"
            elif message is None:
                receipt = await self.bot.send_rich_message(
                    row["chat"],
                    card,
                    message_thread_id=None if row["topic"] == 1 else row["topic"],
                    disable_notification=True,
                )
                message = receipt.message_id
            else:
                await self.bot.edit_message_text(
                    chat_id=row["chat"], message_id=message, rich_message=card
                )
        except TelegramRetryAfter as exc:
            delay, success = float(exc.retry_after), False
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            success = message is not None and "message is not modified" in exc.message.lower()
            if finished and "message to delete not found" in exc.message.lower():
                state, success = "deleted", True
            else:
                state = "ready" if success else "failed"
        except (TelegramAPIError, TimeoutError, OSError):
            state = "unknown" if message is None else "ready"
            delay, success = 10.0, False
        if not success and row["failures"] >= 4 and state == "ready":
            state = "failed"
        await asyncio.to_thread(self.settle, row, state, message, delay, success)

    async def run(self, interval: float) -> None:
        while True:
            await self.deliver_one()
            await asyncio.sleep(interval)
