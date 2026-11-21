"""Storage and Telegram operations used by the ingest use case."""

from collections.abc import Sequence
from typing import Protocol

from peyda_pm_bot.domain.conversations.models import IncomingUpdate


class InboxStore(Protocol):
    async def contains(self, update_id: int) -> bool: ...

    async def add(
        self,
        update_id: int,
        kind: str,
        admission: str,
        reason: str | None,
        received_at: str,
    ) -> None: ...


class MessageStore(Protocol):
    async def next_version(self, chat_id: int, message_id: int) -> int: ...

    async def add(
        self,
        update_id: int,
        chat_id: int,
        topic_id: int,
        message_id: int,
        sender_id: int,
        reply_to_message_id: int | None,
        text: str,
        message_at: str,
        edited_at: str | None,
        version: int,
    ) -> None: ...


class CheckpointStore(Protocol):
    async def current_offset(self) -> int: ...

    async def advance_to(self, next_offset: int) -> None: ...


class HealthNotice(Protocol):
    update_id: int
    attempt_count: int


class OutboxStore(Protocol):
    async def add_health(self, update_id: int) -> None: ...

    async def pending_health(self) -> Sequence[HealthNotice]: ...

    async def record(
        self,
        update_id: int,
        status: str,
        external_message_id: int | None,
        attempt_count: int,
    ) -> None: ...


class IngestStores(Protocol):
    @property
    def inbox(self) -> InboxStore: ...

    @property
    def messages(self) -> MessageStore: ...

    @property
    def checkpoint(self) -> CheckpointStore: ...

    @property
    def outbox(self) -> OutboxStore: ...

    async def commit(self) -> None: ...


class UpdateSource(Protocol):
    async def fetch(self, offset: int | None) -> Sequence[IncomingUpdate]: ...


class HealthSender(Protocol):
    async def send_health(self, chat_id: int, topic_id: int) -> int: ...


class DeliveryError(Exception):
    """The health reply was not confirmed."""


class DeliveryUnknown(DeliveryError):
    """The request may have reached Telegram. Do not send it again automatically."""


class DeliveryRejected(DeliveryError):
    """Telegram refused the reply. Sending it again will not succeed."""


class DeliveryRetry(DeliveryError):
    """Telegram answered that the reply was not accepted and can be tried later."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds
