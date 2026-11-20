"""Scripted demo using the same public tools; not a semantic model evaluation."""

from collections.abc import AsyncIterator

from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
from peyda_pm_bot.application.actions.service import ActionService
from peyda_pm_bot.application.memory.service import MemoryService
from peyda_pm_bot.domain.actions.models import ActionDraft, ActionSpec
from peyda_pm_bot.domain.agent.models import AgentEvent, AgentRequest, AgentResult
from peyda_pm_bot.domain.memory.models import MemoryRecord


class GithubDemoAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.actions: ActionService | None = None
        self.memory: MemoryService | None = None
        self.last_action: ActionDraft | None = None
        self.case_id: str | None = None

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent | AgentResult]:
        if not self.actions or not self.memory or not request.memory_token:
            raise ValueError("Demo tools not wired")
        token = request.memory_token
        source = request.messages[-1].update_id
        if self.case_id is None:
            await self.actions.read(token, "issues", query="Login")
            case = await self.memory.prepare(
                token, "case", MemoryRecord("case", "ورود", source_update_ids=(source,))
            )
            self.case_id = case.record.id
            draft = await self.actions.prepare(
                token,
                "issue",
                ActionSpec(
                    "create_issue",
                    (source,),
                    case_ids=(self.case_id,),
                    title="Login flow",
                    body=(
                        "Problem: login is missing.\nScope: login flow.\n"
                        "Acceptance: login succeeds and failure is shown."
                    ),
                    assignees=("alice",),
                    work_status="ready",
                ),
            )
        else:
            await self.memory.read(token, self.case_id)
            await self.actions.read(token, "issue", number=1)
            await self.actions.read(token, "pulls")
            await self.actions.work(token, "alice")
            draft = await self.actions.prepare(
                token,
                "start",
                ActionSpec(
                    "update_issue",
                    (source,),
                    case_ids=(self.case_id,),
                    issue_number=1,
                    assignees=("alice",),
                    work_status="in_progress",
                ),
            )
        self.last_action = await self.actions.submit(token, draft.id, draft.revision)
        yield AgentResult("silent")
