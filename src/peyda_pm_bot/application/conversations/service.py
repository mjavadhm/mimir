"""Durable ingestion and local health response use cases."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from peyda_pm_bot.application.ports import UowFactory
from peyda_pm_bot.application.work.service import WorkFollowup
from peyda_pm_bot.domain.conversations.events import IncomingUpdate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationPolicy:
    group_id: int
    topic_id: int
    admin_ids: frozenset[int]
    member_ids: frozenset[int] = frozenset()


class Conversations:
    def __init__(
        self,
        uow: UowFactory,
        policy: ConversationPolicy,
        *,
        queue_enabled: bool = False,
        clock: Callable[[], float] = time.time,
        quiet: float = 4,
        ceiling: float = 12,
        followup: WorkFollowup | None = None,
    ) -> None:
        self.queue_enabled = queue_enabled
        self.clock = clock
        self.quiet = quiet
        self.ceiling = ceiling
        self.uow = uow
        self.policy = policy
        self.followup = followup

    async def offset(self) -> int:
        async with self.uow() as unit:
            return await unit.incoming.offset()

    async def ingest(self, updates: list[IncomingUpdate]) -> None:
        # One batch transaction: even a failure on the final item cannot skip earlier input.
        async with self.uow() as unit:
            offset = await unit.incoming.offset()
            for update in sorted(updates, key=lambda item: item.update_id):
                if update.update_id < offset:
                    continue
                message = update.message
                accepted = (
                    message is not None
                    and message.chat_id == self.policy.group_id
                    and message.topic_id == self.policy.topic_id
                )
                await unit.incoming.ingest(update, accepted)
                offset = max(offset, update.update_id + 1)
            await unit.incoming.advance(offset)
        for update in updates:
            logger.info("input committed event_id=%s", update.update_id)

    async def process_one(self) -> bool:
        async with self.uow() as unit:
            update = await unit.incoming.next_input()
            if update is None:
                return False
            message = update.message
            response = None
            if (
                message is not None
                and message.chat_id == self.policy.group_id
                and message.topic_id == self.policy.topic_id
                and message.speaker_id in self.policy.admin_ids
                and message.sender_chat_id is None
                and not message.is_edit
                and message.text == "/health"
            ):
                response = "سرویس فعال است؛ ذخیره‌سازی محلی در دسترس است."
            command, _, argument = (message.text or "").partition(" ") if message else ("", "", "")
            local = command in {
                "/health",
                "/status",
                "/cancel",
                "/retry",
                "/turbo",
                "/cancel_action",
                "/retry_action",
                "/action",
                "/report",
            }
            if command == "/report":
                if (
                    self.followup is not None
                    and message is not None
                    and message.chat_id == self.policy.group_id
                    and message.topic_id == self.policy.topic_id
                    and message.speaker_id in self.policy.admin_ids | self.policy.member_ids
                    and message.sender_chat_id is None
                    and not message.is_edit
                    and not argument.strip()
                ):
                    response = await self.followup.report_text(unit)
            elif local and command != "/health" and self.queue_enabled:
                if (
                    message
                    and message.speaker_id in self.policy.admin_ids
                    and message.sender_chat_id is None
                    and not message.is_edit
                ):
                    try:
                        if command == "/cancel_action":
                            response = await unit.actions.cancel(argument.strip())
                        elif command == "/retry_action":
                            response = await unit.actions.retry(argument.strip())
                        elif command == "/action":
                            action = await unit.actions.current(argument.strip())
                            response = (
                                f"{action.draft.id}: {action.status}; attempts={action.attempts}"
                            )
                        else:
                            response = await unit.queue.control(command, argument.strip())
                    except ValueError:
                        response = (
                            "درخواست کنترل عملیات نامعتبر است؛ شناسه و وضعیت عملیات را بررسی کنید."
                        )

            await unit.incoming.finish_input(update, response)
            if message is not None and message.is_edit:
                await unit.memory.invalidate(message.chat_id, message.message_id)
                await unit.actions.invalidate(
                    await unit.incoming.source_ids(message.chat_id, message.message_id)
                )
            if self.queue_enabled and not local:
                await unit.queue.add(update, self.clock(), self.quiet, self.ceiling)
            return True
