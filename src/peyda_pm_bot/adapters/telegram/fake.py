"""Deterministic transport used by the offline demo and behavioral tests."""

from collections import deque

from peyda_pm_bot.domain.conversations.events import Delivery, IncomingUpdate


class FakeTransport:
    def __init__(self, updates: list[IncomingUpdate] | None = None) -> None:
        self.updates = updates or []
        self.sent: list[Delivery] = []
        self.reactions: dict[tuple[int, int], str | None] = {}
        self.offsets: list[int] = []
        self.send_errors: deque[Exception] = deque()
        self.receive_errors: deque[Exception] = deque()
        self.closed = False

    async def receive(self, offset: int) -> list[IncomingUpdate]:
        self.offsets.append(offset)
        if self.receive_errors:
            raise self.receive_errors.popleft()
        return [item for item in self.updates if item.update_id >= offset][:100]

    async def send(self, delivery: Delivery) -> int:
        if self.send_errors:
            raise self.send_errors.popleft()
        self.sent.append(delivery)
        if delivery.operation == "reaction" and delivery.target_id is not None:
            self.reactions[(delivery.chat_id, delivery.target_id)] = delivery.reaction_emoji
        return delivery.target_id or len(self.sent)

    async def close(self) -> None:
        self.closed = True
