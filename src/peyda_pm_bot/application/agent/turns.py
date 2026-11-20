"""Shared technical authorization, independent of team consensus policy."""

from peyda_pm_bot.application.ports import UnitOfWork
from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.memory.models import MemoryTurn


async def authorize_turn(unit: UnitOfWork, project_id: str, token: str) -> MemoryTurn:
    if not 20 <= len(token) <= 100:
        raise ValueError("Invalid turn grant")
    turn = await unit.turns.authorize(project_id, token)
    if not await unit.queue.wanted(AgentRequest(turn.job_id, turn.revision, turn.generation, ())):
        raise ValueError("Tool turn is no longer active")
    return turn
