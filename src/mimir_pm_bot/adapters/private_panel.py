"""Private Telegram status cards refresh only while their requested turn is active."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from mimir_pm_bot.adapters.rich_text import rich_text
from mimir_pm_bot.application.turns import Control


class PrivatePanel:
    def __init__(
        self,
        bot: Bot,
        admins: frozenset[int],
        interval: float,
        on_error: Callable[[str, Exception], Awaitable[None]] | None = None,
    ) -> None:
        self.bot, self.admins, self.interval = bot, admins, interval
        self.on_error = on_error
        self.refreshes: dict[int, asyncio.Task[None]] = {}

    async def send(
        self,
        control: Control,
        text: str,
        render: Callable[[Control], Awaitable[str]],
        active: Callable[[], int | None],
    ) -> None:
        if control.actor not in self.admins:
            return
        try:
            message = await self.bot.send_rich_message(control.actor, rich_text(text, literal=True))
        except TelegramAPIError as exc:
            if self.on_error:
                await self.on_error("Admin panel delivery", exc)
            return
        if control.command != "/screen":
            return
        old = self.refreshes.pop(control.actor, None)
        if old:
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
        if control.pile is None or active() != control.pile:
            return

        async def refresh() -> None:
            previous = text
            while active() == control.pile:
                await asyncio.sleep(self.interval)
                if active() not in {control.pile, None}:
                    return
                updated = await render(control)
                if updated != previous:
                    try:
                        await self.bot.edit_message_text(
                            rich_message=rich_text(updated, literal=True),
                            chat_id=control.actor,
                            message_id=message.message_id,
                        )
                    except TelegramAPIError as exc:
                        if self.on_error:
                            await self.on_error("Admin panel refresh", exc)
                        return
                    previous = updated

        self.refreshes[control.actor] = asyncio.create_task(refresh())

    async def close(self) -> None:
        for task in self.refreshes.values():
            task.cancel()
        for task in self.refreshes.values():
            with suppress(asyncio.CancelledError):
                await task
