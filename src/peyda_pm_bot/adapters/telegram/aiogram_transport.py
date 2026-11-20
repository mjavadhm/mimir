"""Bot API adapter. Constructed only by explicit wiring, never during import."""

import unicodedata
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import InputRichMessage, ReactionTypeEmoji, ReplyParameters, Update

from peyda_pm_bot.application.ports import PermanentFailure, TemporaryFailure, UnknownOutcome
from peyda_pm_bot.domain.conversations.events import Delivery, IncomingUpdate, MessageEvent


def normalize(update: Update) -> IncomingUpdate:
    message = update.message or update.edited_message
    if message is None:
        return IncomingUpdate(update.update_id, "unsupported", None)
    speaker = message.from_user
    event = MessageEvent(
        chat_id=message.chat.id,
        topic_id=message.message_thread_id,
        message_id=message.message_id,
        speaker_id=speaker.id if speaker else None,
        speaker_name=speaker.full_name if speaker else None,
        sender_chat_id=message.sender_chat.id if message.sender_chat else None,
        reply_to_id=message.reply_to_message.message_id if message.reply_to_message else None,
        text=message.text if message.text is not None else message.caption,
        sent_at=message.date,
        edited_at=datetime.fromtimestamp(message.edit_date, UTC)
        if message.edit_date is not None
        else None,
        is_edit=update.edited_message is not None,
    )
    return IncomingUpdate(update.update_id, "edited_message" if event.is_edit else "message", event)


class AiogramTransport:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def receive(self, offset: int) -> list[IncomingUpdate]:
        try:
            updates = await self.bot.get_updates(
                offset=offset,
                timeout=25,
                limit=100,
                allowed_updates=["message", "edited_message"],
            )
        except TelegramRetryAfter as error:
            raise TemporaryFailure(error.retry_after) from None
        except (TelegramNetworkError, TelegramServerError):
            raise TemporaryFailure() from None
        except (TelegramUnauthorizedError, TelegramForbiddenError, TelegramBadRequest):
            raise PermanentFailure("Telegram receive configuration rejected") from None
        return [normalize(item) for item in updates]

    async def send(self, delivery: Delivery) -> int:
        try:
            if delivery.operation == "reaction":
                if delivery.target_id is None:
                    raise PermanentFailure("Missing reaction target")
                success = await self.bot.set_message_reaction(
                    chat_id=delivery.chat_id,
                    message_id=delivery.target_id,
                    reaction=[]
                    if delivery.reaction_emoji is None
                    else [ReactionTypeEmoji(emoji=delivery.reaction_emoji)],
                    is_big=False,
                )
                if not success:
                    raise PermanentFailure("Reaction rejected")
                return delivery.target_id
            if delivery.operation != "message":
                raise PermanentFailure("Unsupported delivery operation")
            if delivery.target_id is not None:
                await self.bot.edit_message_text(
                    chat_id=delivery.chat_id, message_id=delivery.target_id, text=delivery.text
                )
                return delivery.target_id
            reply = (
                ReplyParameters(
                    message_id=delivery.reply_to_id,
                    allow_sending_without_reply=True,
                )
                if delivery.reply_to_id is not None
                else None
            )
            if delivery.rich_text:
                try:
                    rich_result = await self.bot.send_rich_message(
                        chat_id=delivery.chat_id,
                        message_thread_id=delivery.topic_id,
                        rich_message=InputRichMessage(
                            markdown=delivery.text,
                            is_rtl=first_strong_is_rtl(delivery.text),
                        ),
                        reply_parameters=reply,
                    )
                except (TelegramBadRequest, TelegramNotFound):
                    # The API rejected the rich send. A network failure cannot use this fallback.
                    pass
                else:
                    return rich_result.message_id
            result = await self.bot.send_message(
                chat_id=delivery.chat_id,
                message_thread_id=delivery.topic_id,
                text=delivery.text,
                reply_parameters=reply,
            )
        except TelegramRetryAfter as error:
            raise TemporaryFailure(error.retry_after) from None
        except TelegramBadRequest as error:
            if (
                delivery.operation == "message"
                and delivery.target_id is not None
                and "message is not modified" in error.message.lower()
            ):
                return delivery.target_id
            raise PermanentFailure("Telegram send rejected") from None
        except (TelegramNetworkError, TelegramServerError):
            raise UnknownOutcome("Telegram send outcome uncertain") from None
        except (TelegramForbiddenError, TelegramNotFound, TelegramUnauthorizedError):
            raise PermanentFailure("Telegram send rejected") from None
        return result.message_id

    async def close(self) -> None:
        await self.bot.session.close()


def first_strong_is_rtl(text: str) -> bool | None:
    for character in text:
        direction = unicodedata.bidirectional(character)
        if direction in {"L", "R", "AL"}:
            return direction != "L"
    return None
