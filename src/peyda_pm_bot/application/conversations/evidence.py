"""Public source-evidence contract consumed by memory without repository internals."""

from typing import Protocol

from peyda_pm_bot.domain.memory.models import Evidence


class EvidenceReader(Protocol):
    async def evidence(self, update_ids: tuple[int, ...]) -> list[Evidence]: ...
    async def source_ids(self, chat_id: int, message_id: int) -> tuple[int, ...]: ...
