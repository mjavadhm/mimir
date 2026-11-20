"""Read-only setup probes; discovery never advances Telegram's update offset."""

from dataclasses import dataclass

from aiogram import Bot


@dataclass(frozen=True)
class BotStatus:
    bot_id: int
    username: str | None
    webhook_active: bool
    privacy_disabled: bool
    group_is_forum: bool | None = None
    membership: str | None = None

    @property
    def ready(self) -> bool:
        return (
            not self.webhook_active
            and self.group_is_forum is True
            and self.membership in {"member", "administrator", "creator"}
            and (self.privacy_disabled or self.membership in {"administrator", "creator"})
        )


@dataclass(frozen=True)
class TopicBinding:
    group_id: int
    topic_id: int
    speaker_id: int
    message_id: int


async def inspect_bot(bot: Bot, group_id: int | None = None) -> BotStatus:
    """Check authentication, polling compatibility and optional group membership."""
    me = await bot.get_me(request_timeout=15)
    webhook = await bot.get_webhook_info(request_timeout=15)
    forum = None
    membership = None
    if group_id is not None:
        chat = await bot.get_chat(group_id, request_timeout=15)
        member = await bot.get_chat_member(group_id, me.id, request_timeout=15)
        forum = chat.type == "supergroup" and chat.is_forum is True
        membership = member.status
    return BotStatus(
        me.id,
        me.username,
        bool(webhook.url),
        me.can_read_all_group_messages is True,
        forum,
        membership,
    )


async def discover_topic(bot: Bot, marker: str = "/peyda_setup") -> list[TopicBinding]:
    """Return explicit setup-message coordinates, without ACK, replies or role assignment.

    Run with the live poller stopped. Only the first 100 pending updates are visible;
    this probe intentionally refuses to skip or acknowledge an older backlog.
    """
    updates = await bot.get_updates(
        offset=None,
        limit=100,
        timeout=0,
        request_timeout=15,
        allowed_updates=["message", "edited_message"],
    )
    bindings: list[TopicBinding] = []
    for update in updates:
        message = update.message
        if (
            message is not None
            and message.chat.type == "supergroup"
            and message.message_thread_id is not None
            and message.from_user is not None
            and not message.from_user.is_bot
            and message.sender_chat is None
            and (message.text or "").split("@", 1)[0] == marker
        ):
            bindings.append(
                TopicBinding(
                    message.chat.id,
                    message.message_thread_id,
                    message.from_user.id,
                    message.message_id,
                )
            )
    return bindings
