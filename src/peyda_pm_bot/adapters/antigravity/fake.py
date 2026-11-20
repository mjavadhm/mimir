"""Scriptable fake implementing exactly the public agent contract."""

from collections.abc import AsyncIterator

from peyda_pm_bot.domain.agent.models import AgentEvent, AgentRequest, AgentResult
from peyda_pm_bot.domain.agent.models import AgentProgress


class FakeAgent:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []
        self.results: list[AgentResult] = []
        self.active = 0
        self.peak = 0
        self.turbo = False

    async def connect(self, session_id: str | None) -> str:
        return session_id or "fake-shared-session"

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent | AgentResult]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.requests.append(request)
        try:
            yield AgentEvent("activity")
            yield self.results.pop(0) if self.results else AgentResult("silent")
        finally:
            self.active -= 1

    async def cancel(self) -> None:
        return None

    def progress(self) -> AgentProgress:
        return AgentProgress()

    async def set_turbo(self, enabled: bool) -> bool:
        self.turbo = enabled
        return True

    async def close(self) -> None:
        return None
