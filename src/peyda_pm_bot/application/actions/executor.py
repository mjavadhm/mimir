"""Serial external writes, conservative reconciliation, durable outcomes."""

import asyncio
import time

from peyda_pm_bot.application.actions.errors import GithubPermanent, GithubTemporary, GithubUnknown
from peyda_pm_bot.application.actions.ports import Github
from peyda_pm_bot.application.ports import UowFactory
from peyda_pm_bot.domain.actions.models import ActionRun, RemoteComment, RemoteIssue, marker


class ActionExecutor:
    def __init__(self, uow: UowFactory, github: Github) -> None:
        self.uow = uow
        self.github = github
        self.lock = asyncio.Lock()
        self.initialized = False

    async def run_one(self) -> bool:
        async with self.lock:
            if not self.initialized:
                async with self.uow() as unit:
                    await unit.actions.bind_repository(self.github.repository)
                    await unit.actions.recover()
                self.initialized = True
            async with self.uow() as unit:
                run = await unit.actions.claim(time.time())
            if run is None:
                return False
            status = "unknown"
            remote: RemoteIssue | RemoteComment | None = None
            detail = ""
            due = 0.0
            try:
                async with asyncio.timeout(30):
                    if run.draft.repository.casefold() != self.github.repository.casefold():
                        raise GithubPermanent("Action belongs to another repository")
                    if run.status == "unknown":
                        remote = await self._reconcile(run)
                        status = "succeeded" if remote else "unknown"
                        detail = (
                            "Reconciled accepted operation"
                            if remote
                            else "Outcome unresolved; no resend performed"
                        )
                        due = (
                            time.time() + min(60 * 2 ** (run.attempts - 1), 3600)
                            if run.attempts - run.retry_from < 5
                            else 253402300799
                        )
                    else:
                        status, remote, detail = await self._execute(run)
            except GithubTemporary as error:
                status = (
                    "unknown"
                    if run.status == "unknown"
                    else "ready"
                    if run.attempts - run.retry_from < 3
                    else "failed"
                )
                detail = "Rate limit or definitely unaccepted request; no ambiguous write repeated"
                due = time.time() + max(error.retry_after, 2 ** min(run.attempts, 6))
                if status == "unknown" and run.attempts - run.retry_from >= 5:
                    due = 253402300799
            except (GithubUnknown, TimeoutError):
                status = "unknown"
                detail = "Write may have been accepted; reconcile before retry"
                due = time.time() + 1
            except GithubPermanent as error:
                status = "unknown" if run.status == "unknown" else "failed"
                detail = str(error)
                due = 253402300799 if status == "unknown" else 0
            except ValueError:
                status = "unknown" if run.status == "unknown" else "failed"
                detail = "Invalid remote response or action data; no automatic resend"
                due = 253402300799 if status == "unknown" else 0
            except asyncio.CancelledError:
                # Committed executing claim becomes unknown at the next startup.
                raise
            async with self.uow() as unit:
                await unit.actions.result(run.draft.id, status, remote, detail, due)
                if isinstance(remote, RemoteIssue):
                    await unit.work.observe(remote, run.draft.spec)
                if status not in {"ready"}:
                    await unit.outgoing.action_notice(run, status, remote, detail)
            return True

    async def _execute(self, run: ActionRun) -> tuple[str, RemoteIssue | RemoteComment | None, str]:
        draft = run.draft
        spec = draft.spec
        async with self.uow() as unit:
            current = await unit.actions.current(draft.id)
            if current.cancel_requested:
                return "cancelled", None, "Cancelled before send"
            evidence = await unit.memory.evidence(spec.source_update_ids)
            if {item.update_id for item in evidence} != set(spec.source_update_ids) or any(
                item.superseded for item in evidence
            ):
                return "stale", None, "Source evidence changed"
        fresh: RemoteIssue | RemoteComment
        if spec.issue_number:
            issue = await self.github.issue(spec.issue_number)
            if issue.is_pull_request:
                return "failed", None, "PR mutations are outside scope"
            if spec.kind == "edit_comment":
                assert spec.comment_id is not None
                fresh = await self.github.comment(spec.issue_number, spec.comment_id)
                if fresh.author.casefold() != self.github.bot_login.casefold():
                    return "failed", None, "Comment is not owned by the bot"
            else:
                fresh = issue
            if fresh != draft.base:
                return "stale", None, "GitHub changed since the draft; refresh required"
        labels, complete = await self.github.labels()
        needed = set(spec.add_labels) | set(spec.remove_labels)
        status_labels = self.github.status_labels
        if spec.work_status:
            needed.add(status_labels.get(spec.work_status, ""))
        if needed and (not complete or not needed.issubset(labels)):
            return "failed", None, "Label configuration changed"
        if spec.assignees:
            users, complete = await self.github.assignees()
            if not complete or not set(spec.assignees).issubset(users):
                return "failed", None, "Assignee availability changed"
        async with self.uow() as unit:
            current = await unit.actions.current(draft.id)
            if current.cancel_requested:
                return "cancelled", None, "Cancelled before send"
            if current.draft.revision != draft.revision:
                return "stale", None, "Draft revision changed"
            evidence = await unit.memory.evidence(spec.source_update_ids)
            if any(item.superseded for item in evidence):
                return "stale", None, "Source edited before send"
        remote = await self.github.mutate(draft)
        if isinstance(remote, RemoteIssue) and remote.is_pull_request:
            raise GithubUnknown("Unexpected PR result from issue write")
        warnings = []
        if isinstance(remote, RemoteIssue):
            if spec.title is not None and remote.title != spec.title:
                warnings.append("title not fully applied")
            if spec.body is not None and not remote.body.startswith(spec.body):
                warnings.append("body not fully applied")
            if (
                spec.work_status
                and self.github.status_labels[spec.work_status] not in remote.labels
            ):
                warnings.append("work status label not applied")
            expected_state = (
                ("closed" if spec.work_status == "done" else "open")
                if spec.work_status
                else spec.state
            )
            if expected_state is not None and remote.state != expected_state:
                warnings.append("issue state not applied")
            if set(spec.remove_labels).intersection(remote.labels):
                warnings.append("labels not removed")
            if spec.assignees is not None and set(remote.assignees) != set(spec.assignees):
                warnings.append("assignees not fully applied")
            if not set(spec.add_labels).issubset(remote.labels):
                warnings.append("labels not fully applied")
        return (
            "succeeded",
            remote,
            ("Partial result: " + ", ".join(warnings))
            if warnings
            else "GitHub operation confirmed",
        )

    async def _reconcile(self, run: ActionRun) -> RemoteIssue | RemoteComment | None:
        spec = run.draft.spec
        tag = marker(run.draft.id)
        matches: list[RemoteIssue | RemoteComment]
        if spec.kind == "create_issue":
            issues, complete = await self.github.issues(tag)
            matches = [
                item
                for item in issues
                if tag in item.body
                and not item.is_pull_request
                and item.author.casefold() == self.github.bot_login.casefold()
            ]
        elif spec.kind == "add_comment":
            assert spec.issue_number is not None
            comments, complete = await self.github.comments(spec.issue_number)
            matches = [
                item
                for item in comments
                if tag in item.body and item.author.casefold() == self.github.bot_login.casefold()
            ]
        elif spec.kind == "update_issue":
            assert spec.issue_number is not None
            issue = await self.github.issue(spec.issue_number)
            complete = True
            matches = [issue] if tag in issue.body and not issue.is_pull_request else []
        else:
            assert spec.issue_number is not None and spec.comment_id is not None
            comment = await self.github.comment(spec.issue_number, spec.comment_id)
            complete = True
            matches = (
                [comment]
                if tag in comment.body
                and comment.author.casefold() == self.github.bot_login.casefold()
                else []
            )
        return matches[0] if complete and len(matches) == 1 else None
