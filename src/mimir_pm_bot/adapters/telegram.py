import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import Update

from mimir_pm_bot.adapters.rich_text import rich_text
from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.application.destination import SetupChange
from mimir_pm_bot.domain.conversations import Event, Version


def normalize(
    update: Update, group: int, topic: int, arrived: datetime, admins: frozenset[int] = frozenset()
) -> Event:
    m = update.edited_message or update.message
    if (
        m
        and m == update.message
        and m.chat.type == "private"
        and m.from_user
        and m.chat.id == m.from_user.id
        and m.chat.id in admins
        and m.text
        and m.text.split(maxsplit=1)[:1] in (["/screen"], ["/limits"], ["/cancel"], ["/model"])
    ):
        return Event(update.update_id, arrived, None, (m.chat.id, m.text))
    if (
        m
        and m.text
        and m.text.partition(" ")[0]
        in {"/screen", "/limits", "/cancel", "/model", "/settings", "/bind", "/abort"}
    ):
        return Event(update.update_id, arrived, None)
    version = None
    if (
        m
        and m.chat.id == group
        and (m.message_thread_id or (1 if m.chat.is_forum else None)) == topic
    ):
        if m.sender_chat:
            kind, identity, name = "chat", m.sender_chat.id, m.sender_chat.title
        elif m.from_user:
            kind, identity, name = "user", m.from_user.id, m.from_user.full_name
        else:
            kind, identity, name = "unknown", None, None
        version = Version(
            group,
            topic,
            m.message_id,
            kind,
            identity,
            name,
            m.text,
            m.caption,
            m.date,
            datetime.fromtimestamp(m.edit_date, UTC) if m.edit_date is not None else None,
            m.reply_to_message.message_id if m.reply_to_message else None,
            update.edited_message is not None,
        )
    return Event(update.update_id, arrived, version)


class Telegram:
    def __init__(
        self, bot: Bot, group: int, topic: int, timeout: int, admins: frozenset[int] = frozenset()
    ) -> None:
        self.on_error: Callable[[str, Exception], Awaitable[None]] | None = None
        self.admins = admins
        self.bot, self.group, self.topic, self.timeout = bot, group, topic, timeout

    async def updates(self, offset: int | None) -> list[Update]:
        backoff = 1
        while True:
            try:
                updates = await self.bot.get_updates(
                    offset=offset,
                    timeout=self.timeout,
                    allowed_updates=["message", "edited_message"],
                )
                break
            except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter) as exc:
                if backoff == 1 and self.on_error:
                    await self.on_error("Telegram polling", exc)
                delay = exc.retry_after if isinstance(exc, TelegramRetryAfter) else backoff
                backoff = min(30, backoff * 2)
                await asyncio.sleep(delay)
        return updates

    async def fetch(self, offset: int | None) -> list[Event]:
        updates = await self.updates(offset)
        arrived = datetime.now(UTC)
        return [normalize(u, self.group, self.topic, arrived, self.admins) for u in updates]

    async def collect_managed(self, store: Store) -> None:
        self.group, self.topic = await asyncio.to_thread(store.destination)
        while True:
            offset = await asyncio.to_thread(store.checkpoint)
            for update in await self.updates(offset):
                await self.ingest(store, update)

    async def ingest(self, store: Store, update: Update) -> None:
        now = datetime.now(UTC)
        message = update.message
        if (
            message
            and message.chat.type == "private"
            and message.from_user
            and message.from_user.id == message.chat.id
            and message.chat.id in self.admins
            and message.text
        ):
            actor, text = message.chat.id, message.text.strip()
            change, reply = await self.destination_command(store, actor, text)
            if reply:
                if change and change.step == "activate":
                    if await asyncio.to_thread(store.reserve_probe, update.update_id):
                        try:
                            receipt = await self.bot.send_rich_message(
                                change.group,
                                rich_text("Mimir · Topic connection check", literal=True),
                                message_thread_id=change.topic if change.topic != 1 else None,
                            )
                            verified = (
                                receipt.chat.id == change.group
                                and (receipt.message_thread_id or 1) == change.topic
                            )
                        except TelegramAPIError:
                            verified = False
                    else:
                        verified = False
                    if not verified:
                        change = None
                        reply = "تاپیک تأیید نشد. مقصد قبلی حفظ شد. شناسه را دوباره بفرست."
                await asyncio.to_thread(store.record, Event(update.update_id, now, None), change)
                self.group, self.topic = await asyncio.to_thread(store.destination)
                try:
                    await self.bot.send_rich_message(actor, rich_text(reply, literal=True))
                except TelegramAPIError as exc:
                    if self.on_error:
                        await self.on_error("Admin settings reply", exc)
                return
        event = normalize(update, self.group, self.topic, now, self.admins)
        await asyncio.to_thread(store.record, event)

    async def destination_command(
        self, store: Store, actor: int, text: str
    ) -> tuple[SetupChange | None, str]:
        if text in {"/bind", "/settings"}:
            current = f"{self.group} / {self.topic}" if self.group else "تنظیم نشده"
            return SetupChange(actor, "begin"), (
                f"مقصد فعلی: {current}\nشناسهٔ عددی گروه جدید را بفرست. برای انصراف /abort را بزن."
            )
        active, group = await asyncio.to_thread(store.setup, actor)
        if not active:
            return None, ""
        if text == "/abort":
            return SetupChange(actor, "abort"), "تنظیم مقصد لغو شد."
        if text.startswith("/"):
            return None, ""
        try:
            value = int(text)
        except ValueError:
            value = 0
        if group is None:
            if not -(2**52) < value < 0:
                return None, "شناسهٔ عددی منفی گروه را بفرست."
            try:
                chat = await self.bot.get_chat(value)
                if chat.id != value or chat.type != "supergroup" or not chat.is_forum:
                    return None, "این گروه تاپیک ندارد. شناسهٔ یک گروه دارای تاپیک را بفرست."
            except TelegramAPIError:
                return (
                    None,
                    "گروه در دسترس نیست. شناسه و عضویت بات را بررسی کن.",
                )
            return SetupChange(
                actor, "group", value
            ), f"گروه {chat.title} انتخاب شد. شناسهٔ عددی تاپیک را بفرست."
        if not 0 < value < 2**31:
            return None, "شناسهٔ عددی مثبت تاپیک را بفرست."
        return SetupChange(
            actor, "activate", group, value
        ), f"میمیر در گروه {group} و تاپیک {value} فعال شد."
