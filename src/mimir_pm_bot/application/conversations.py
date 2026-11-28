from collections.abc import Sequence
from typing import Protocol

from mimir_pm_bot.domain.conversations import Event


class Inbox(Protocol):
    def checkpoint(self) -> int | None: ...
    def record(self, event: Event) -> int: ...


class Source(Protocol):
    async def fetch(self, offset: int | None) -> Sequence[Event]: ...
