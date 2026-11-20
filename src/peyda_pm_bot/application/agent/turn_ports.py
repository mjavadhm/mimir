"""One grant lifecycle shared by all agent tools."""

from typing import Protocol

from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.memory.models import MemoryPolicy, MemoryTurn


class TurnStore(Protocol):
    async def begin(self, request: AgentRequest, policy: MemoryPolicy, project_id: str) -> str: ...
    async def authorize(self, project_id: str, token: str) -> MemoryTurn: ...
