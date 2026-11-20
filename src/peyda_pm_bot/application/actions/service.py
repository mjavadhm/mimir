"""Intent preparation and bounded reads; all writes are delegated to the executor."""

import asyncio
import hashlib
import json
from dataclasses import asdict

from peyda_pm_bot.application.actions.ports import Github
from peyda_pm_bot.application.agent.turns import authorize_turn
from peyda_pm_bot.application.ports import UowFactory
from peyda_pm_bot.domain.actions.models import (
    ActionDraft,
    ActionSpec,
    RemoteComment,
    RemoteIssue,
    validate_spec,
)
from peyda_pm_bot.domain.work.models import STATUSES


class ActionService:
    def __init__(
        self,
        uow: UowFactory,
        github: Github,
        project_id: str,
        status_labels: dict[str, str],
        member_logins: dict[int, str] | None = None,
    ) -> None:
        if github.status_labels != status_labels:
            raise ValueError("Provider and service status mappings must agree")
        self.member_logins = member_logins or {}
        self.uow = uow
        self.github = github
        self.project_id = project_id
        self.status_labels = status_labels
        self.code_refs: dict[str, str] = {}
        self.code_lock = asyncio.Lock()

    async def check(self, token: str) -> None:
        async with self.uow() as unit:
            await authorize_turn(unit, self.project_id, token)
            await unit.actions.bind_repository(self.github.repository)

    async def prepare(
        self,
        token: str,
        key: str,
        spec: ActionSpec,
        action_id: str | None = None,
        expected_revision: int = 0,
    ) -> ActionDraft:
        validate_spec(spec)
        await self.check(token)
        base: RemoteIssue | RemoteComment | None = None
        if spec.issue_number:
            issue = await self.github.issue(spec.issue_number)
            if issue.is_pull_request:
                raise ValueError("Pull requests are read-only in this phase")
            base = issue
        if spec.kind == "edit_comment":
            assert spec.issue_number is not None and spec.comment_id is not None
            comment = await self.github.comment(spec.issue_number, spec.comment_id)
            base = comment
            if comment.author.casefold() != self.github.bot_login.casefold():
                raise ValueError("Only the configured bot author may have comments edited")
        if spec.add_labels or spec.remove_labels or spec.work_status:
            labels, complete = await self.github.labels()
            needed = set(spec.add_labels) | set(spec.remove_labels)
            if spec.work_status:
                needed.add(self.status_labels[spec.work_status])
            if not complete or not needed.issubset(labels):
                raise ValueError("Unknown label or incomplete label listing")
        if spec.assignees:
            users, complete = await self.github.assignees()
            if not complete or not set(spec.assignees).issubset(users):
                raise ValueError("Unknown assignee or incomplete assignee listing")
        async with self.uow() as unit:
            turn = await authorize_turn(unit, self.project_id, token)
            sources = await unit.memory.evidence(spec.source_update_ids)
            if {source.update_id for source in sources} != set(spec.source_update_ids) or any(
                source.superseded for source in sources
            ):
                raise ValueError("Invalid or stale action sources")
            cases = {view.record.id: view for view in await unit.memory.views(turn)}
            if any(
                identity not in cases
                or cases[identity].record.kind != "case"
                or cases[identity].record.status != "active"
                for identity in spec.case_ids
            ):
                raise ValueError("Unknown case")
            return await unit.actions.prepare(
                turn, key, spec, base, action_id, expected_revision, self.github.repository
            )

    async def submit(self, token: str, action_id: str, revision: int) -> ActionDraft:
        async with self.uow() as unit:
            turn = await authorize_turn(unit, self.project_id, token)
            return await unit.actions.submit(turn, action_id, revision)

    async def status(self, token: str, action_id: str) -> str:
        async with self.uow() as unit:
            turn = await authorize_turn(unit, self.project_id, token)
            draft = await unit.actions.visible(turn, action_id)
            if not draft.published:
                return json.dumps(
                    {"draft": asdict(draft), "status": "staged_until_successful_turn"}
                )
            return json.dumps(asdict(await unit.actions.current(action_id)))

    async def cancel(self, token: str, action_id: str) -> str:
        async with self.uow() as unit:
            await authorize_turn(unit, self.project_id, token)
            return await unit.actions.cancel(action_id)

    async def read(
        self,
        token: str,
        resource: str,
        query: str = "",
        number: int = 0,
        path: str = "",
        ref: str = "",
        offset: int = 0,
        limit: int = 8000,
    ) -> str:
        await self.check(token)
        if resource == "members":
            return json.dumps({str(key): value for key, value in self.member_logins.items()})
        if resource == "issues":
            issues, complete = await self.github.issues(query)
            async with self.uow() as unit:
                await authorize_turn(unit, self.project_id, token)
                for issue in issues:
                    if not issue.is_pull_request:
                        await unit.work.observe(issue)
            value = json.dumps(
                {"items": [asdict(issue) for issue in issues], "complete": complete},
                ensure_ascii=False,
            )
        elif resource == "issue":
            issue = await self.github.issue(number)
            async with self.uow() as unit:
                await authorize_turn(unit, self.project_id, token)
                if not issue.is_pull_request:
                    await unit.work.observe(issue)
            value = json.dumps(asdict(issue), ensure_ascii=False)
        elif resource == "comments":
            comments, complete = await self.github.comments(number)
            value = json.dumps(
                {"items": [asdict(item) for item in comments], "complete": complete},
                ensure_ascii=False,
            )
        elif resource in {"labels", "assignees"}:
            items, complete = await (
                self.github.labels() if resource == "labels" else self.github.assignees()
            )
            value = json.dumps({"items": items, "complete": complete})
        else:
            if resource in {"tree", "file"}:
                async with self.code_lock:
                    key = hashlib.sha256(token.encode()).hexdigest()
                    pinned = self.code_refs.get(key)
                    if resource in {"tree", "file"} and pinned:
                        if ref and ref != pinned:
                            raise ValueError("Code snapshot is already pinned for this turn")
                        ref = pinned
                    value = await self.github.reads(resource, number, path, ref, offset, limit)
                    if resource in {"tree", "file"}:
                        resolved = json.loads(value).get("ref")
                        if resolved:
                            if len(self.code_refs) > 64:
                                self.code_refs.clear()
                            self.code_refs[key] = resolved
            else:
                value = await self.github.reads(resource, number, path, ref, offset, limit)
        await self.check(token)
        if len(value) > 12000:
            # Never silently clip data or mistake incomplete results for absence.
            return json.dumps(
                {
                    "complete": False,
                    "error": "response_budget",
                    "hint": "Narrow the query or file range",
                }
            )
        return value

    async def work(self, token: str, login: str | None = None) -> str:
        async with self.uow() as unit:
            await authorize_turn(unit, self.project_id, token)
            items = await unit.work.list(self.status_labels)
        selected = [item for item in items if login is None or login in item.issue.assignees]
        value = json.dumps(
            {
                "items": [asdict(item) for item in selected],
                "freshness": "on-demand projection; refresh issues before starting work",
                "statuses": STATUSES,
            },
            ensure_ascii=False,
        )
        return value if len(value) <= 12000 else '{"complete":false,"error":"narrow_work_query"}'
