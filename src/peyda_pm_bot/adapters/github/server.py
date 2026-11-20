"""MCP tools hosted in the main process: no credential-bearing agent subprocess."""

import json
from dataclasses import asdict
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from peyda_pm_bot.application.actions.service import ActionService
from peyda_pm_bot.domain.actions.models import ActionSpec


class SpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["create_issue", "update_issue", "add_comment", "edit_comment"]
    source_update_ids: list[int] = Field(min_length=1, max_length=100)
    case_ids: list[str] = Field(default_factory=list, max_length=20)
    issue_number: int | None = Field(default=None, gt=0)
    comment_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=256)
    body: str | None = Field(default=None, max_length=50000)
    assignees: list[str] | None = Field(default=None, max_length=10)
    add_labels: list[str] = Field(default_factory=list, max_length=30)
    remove_labels: list[str] = Field(default_factory=list, max_length=30)
    state: Literal["open", "closed"] | None = None
    work_status: (
        Literal["backlog", "ready", "in_progress", "in_review", "blocked", "done"] | None
    ) = None
    due_at: str | None = None
    dependencies: list[int] | None = Field(default=None, max_length=30)
    priority: str | None = None

    def domain(self) -> ActionSpec:
        return TypeAdapter(ActionSpec).validate_python(self.model_dump())


def make_server(service: ActionService) -> FastMCP:
    server = FastMCP(
        "peyda-github",
        host="127.0.0.1",
        stateless_http=True,
        json_response=True,
        log_level="WARNING",
    )

    @server.tool()
    async def github_read(
        turn_token: str,
        resource: Literal[
            "issues",
            "issue",
            "comments",
            "labels",
            "assignees",
            "members",
            "pulls",
            "pull",
            "pull_paths",
            "tree",
            "file",
        ],
        query: str = "",
        number: int = 0,
        path: str = "",
        ref: str = "",
        offset: int = 0,
        limit: int = 8000,
    ) -> str:
        """Read the configured repository only. File reads pin one commit SHA per turn.

        PRs expose metadata and changed paths only, not diffs or reviews. Check complete and
        next_offset; absence in incomplete results is not proof of no matching work.
        """
        return await service.read(turn_token, resource, query, number, path, ref, offset, limit)

    @server.tool()
    async def github_prepare(
        turn_token: str,
        operation_key: str,
        spec: SpecInput,
        action_id: str | None = None,
        expected_revision: int = 0,
    ) -> str:
        """Stage an issue-management draft. No GitHub mutation occurs; submit separately.

        Include problem, expected outcome, scope and acceptance criteria in issue bodies.
        Assignees must be explicit verified logins, never guessed from display names.
        """
        draft = await service.prepare(
            turn_token, operation_key, spec.domain(), action_id, expected_revision
        )
        return json.dumps(asdict(draft), ensure_ascii=False)

    @server.tool()
    async def github_submit(turn_token: str, action_id: str, revision: int) -> str:
        """Stage execution of exactly this draft revision, after following team prompt policy.

        This is not human-approval verification. Work starts only after the agent turn succeeds.
        """
        return json.dumps(
            asdict(await service.submit(turn_token, action_id, revision)), ensure_ascii=False
        )

    @server.tool()
    async def github_action(turn_token: str, action_id: str, cancel: bool = False) -> str:
        """Read action state, or cancel before sending. In-flight writes require reconciliation."""
        return (
            await service.cancel(turn_token, action_id)
            if cancel
            else await service.status(turn_token, action_id)
        )

    @server.tool()
    async def work_status(turn_token: str, login: str | None = None) -> str:
        """Read known tasks, case links, dependencies, blockers and observation time.

        Refresh issues/PR metadata before assessing overlap or starting work. Projection is
        on-demand; semantic overlap and team agreement are judgments for the same agent.
        """
        return await service.work(turn_token, login)

    return server
