"""Deterministic external-service fake, including lost replies after accepted writes."""

import json
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, replace
from typing import cast

from peyda_pm_bot.application.actions.errors import GithubPermanent, GithubUnknown
from peyda_pm_bot.domain.actions.models import ActionDraft, RemoteComment, RemoteIssue, marker
from peyda_pm_bot.domain.work.models import STATUSES, BranchProtection, PullSummary, desired_fields


class FakeGithub:
    def __init__(self, repository: str = "example/project", bot_login: str = "pm-bot") -> None:
        self.repository = repository
        self.bot_login = bot_login
        self.status_labels = {status: f"pm:{status}" for status in STATUSES}
        self.issue_data: dict[int, RemoteIssue] = {}
        self.comment_data: dict[int, RemoteComment] = {}
        self.pull_data: dict[int, PullSummary] = {}
        self.protection = BranchProtection()
        self.available_labels = list(self.status_labels.values()) + ["bug", "team"]
        self.users = ["alice", "bob", "carol"]
        self.complete = True
        self.mutations = 0
        self.lose_next_reply = False
        self.errors: deque[Exception] = deque()
        self.closed = False

    async def issue(self, number: int) -> RemoteIssue:
        if number not in self.issue_data:
            raise GithubPermanent("Issue not found")
        return self.issue_data[number]

    async def comment(self, issue_number: int, comment_id: int) -> RemoteComment:
        value = self.comment_data.get(comment_id)
        if value is None or value.issue_number != issue_number:
            raise GithubPermanent("Comment not found")
        return value

    async def issues(self, query: str = "") -> tuple[list[RemoteIssue], bool]:
        return [
            item
            for item in self.issue_data.values()
            if query.casefold() in (item.title + " " + item.body).casefold()
        ], self.complete

    async def comments(self, issue_number: int) -> tuple[list[RemoteComment], bool]:
        return [
            item for item in self.comment_data.values() if item.issue_number == issue_number
        ], self.complete

    async def labels(self) -> tuple[list[str], bool]:
        return self.available_labels, self.complete

    async def assignees(self) -> tuple[list[str], bool]:
        return self.users, self.complete

    async def pull_summaries(self) -> tuple[list[PullSummary], bool]:
        return list(self.pull_data.values()), self.complete

    async def branch_protection(self) -> BranchProtection:
        return self.protection

    async def mutate(self, draft: ActionDraft) -> RemoteIssue | RemoteComment:
        if self.errors:
            raise self.errors.popleft()
        self.mutations += 1
        spec = draft.spec
        stamp = f"2026-11-18T12:00:{self.mutations:02}Z"
        tag = marker(draft.id)
        result: RemoteIssue | RemoteComment
        if spec.kind == "create_issue":
            number = max(self.issue_data, default=0) + 1
            empty = RemoteIssue(
                number,
                "",
                "",
                "open",
                (),
                (),
                stamp,
                f"https://github.com/{self.repository}/issues/{number}",
            )
            fields = desired_fields(spec, empty, self.status_labels)
            result = replace(
                empty,
                title=spec.title or "",
                body=(spec.body or "") + "\n\n" + tag,
                labels=tuple(cast(Iterable[str], fields.get("labels", []))),
                assignees=spec.assignees or (),
                state=str(fields.get("state", "open")),
                author=self.bot_login,
            )
            self.issue_data[number] = result
        elif spec.kind == "update_issue":
            assert spec.issue_number is not None
            old = await self.issue(spec.issue_number)
            fields = desired_fields(spec, old, self.status_labels)
            result = replace(
                old,
                title=str(fields.get("title", old.title)),
                body=str(fields.get("body", old.body)) + "\n\n" + tag,
                labels=tuple(cast(Iterable[str], fields.get("labels", old.labels))),
                assignees=spec.assignees if spec.assignees is not None else old.assignees,
                state=str(fields.get("state", old.state)),
                updated_at=stamp,
            )
            self.issue_data[old.number] = result
        else:
            assert spec.issue_number is not None
            identity = (
                max(self.comment_data, default=0) + 1
                if spec.kind == "add_comment"
                else spec.comment_id
            )
            assert identity is not None
            result = RemoteComment(
                identity,
                spec.issue_number,
                (spec.body or "") + "\n\n" + tag,
                self.bot_login,
                stamp,
                f"https://github.com/{self.repository}/issues/{spec.issue_number}#issuecomment-{identity}",
            )
            self.comment_data[identity] = result
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise GithubUnknown("Reply lost after successful mutation")
        return result

    async def reads(
        self,
        resource: str,
        number: int = 0,
        path: str = "",
        ref: str = "",
        offset: int = 0,
        limit: int = 8000,
    ) -> str:
        if resource == "pulls":
            return json.dumps(
                {
                    "items": [
                        asdict(item) for item in self.issue_data.values() if item.is_pull_request
                    ],
                    "complete": self.complete,
                }
            )
        if resource == "pull":
            return json.dumps(asdict(await self.issue(number)))
        if resource == "pull_paths":
            return json.dumps({"items": [], "complete": self.complete})
        if resource == "tree":
            return json.dumps({"ref": ref or "a" * 40, "paths": ["src/main.py"], "complete": True})
        if resource == "file":
            return json.dumps(
                {
                    "ref": ref or "a" * 40,
                    "path": path,
                    "text": "# offline source\n",
                    "complete": True,
                }
            )
        raise ValueError("Unknown read resource")

    async def close(self) -> None:
        self.closed = True
