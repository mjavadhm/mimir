"""Bounded issue-management intents; no HTTP, credentials or consensus rules."""

from dataclasses import dataclass
from typing import Literal

ActionKind = Literal["create_issue", "update_issue", "add_comment", "edit_comment"]


@dataclass(frozen=True)
class ActionSpec:
    kind: ActionKind
    source_update_ids: tuple[int, ...]
    case_ids: tuple[str, ...] = ()
    issue_number: int | None = None
    comment_id: int | None = None
    title: str | None = None
    body: str | None = None
    assignees: tuple[str, ...] | None = None
    add_labels: tuple[str, ...] = ()
    remove_labels: tuple[str, ...] = ()
    state: Literal["open", "closed"] | None = None
    work_status: (
        Literal["backlog", "ready", "in_progress", "in_review", "blocked", "done"] | None
    ) = None
    due_at: str | None = None
    dependencies: tuple[int, ...] | None = None
    priority: str | None = None


@dataclass(frozen=True)
class RemoteIssue:
    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    updated_at: str
    url: str
    is_pull_request: bool = False
    author: str = ""


@dataclass(frozen=True)
class RemoteComment:
    id: int
    issue_number: int
    body: str
    author: str
    updated_at: str
    url: str


@dataclass(frozen=True)
class ActionDraft:
    id: str
    revision: int
    spec: ActionSpec
    base: RemoteIssue | RemoteComment | None = None
    submitted: bool = False
    published: bool = False
    repository: str = ""


@dataclass(frozen=True)
class ActionRun:
    draft: ActionDraft
    status: str
    attempts: int
    cancel_requested: bool = False
    retry_from: int = 0


def validate_spec(spec: ActionSpec) -> None:
    if spec.kind not in {"create_issue", "update_issue", "add_comment", "edit_comment"}:
        raise ValueError("Unsupported action")
    if spec.state not in {None, "open", "closed"} or spec.work_status not in {
        None,
        "backlog",
        "ready",
        "in_progress",
        "in_review",
        "blocked",
        "done",
    }:
        raise ValueError("Invalid issue state")
    if spec.kind == "create_issue" and (spec.state == "closed" or spec.work_status == "done"):
        raise ValueError("Create an open issue; close it with a separate update")
    if spec.priority is not None and len(spec.priority) > 100:
        raise ValueError("Priority value exceeds limit")
    if not spec.source_update_ids or len(spec.source_update_ids) > 100 or len(spec.case_ids) > 20:
        raise ValueError("Action needs bounded source evidence")
    if spec.kind == "create_issue":
        if not spec.title or not spec.title.strip() or spec.body is None:
            raise ValueError("New issue needs title and structured body")
        if spec.issue_number is not None or spec.comment_id is not None:
            raise ValueError("Create has no existing target")
    elif spec.issue_number is None or spec.issue_number <= 0:
        raise ValueError("Issue number required")
    if spec.kind in {"add_comment", "edit_comment"}:
        if not spec.body or not spec.body.strip():
            raise ValueError("Comment body required")
        if any(
            (
                spec.title is not None,
                spec.assignees is not None,
                spec.add_labels,
                spec.remove_labels,
                spec.state,
                spec.work_status,
                spec.due_at,
                spec.dependencies,
                spec.priority,
            )
        ):
            raise ValueError("Comment action cannot modify issue fields")
    if spec.kind == "edit_comment" and (spec.comment_id is None or spec.comment_id <= 0):
        raise ValueError("Comment ID required")
    if spec.kind != "edit_comment" and spec.comment_id is not None:
        raise ValueError("Unexpected comment ID")
    if spec.title is not None and (not spec.title.strip() or len(spec.title) > 256):
        raise ValueError("Invalid title")
    if spec.body is not None and len(spec.body) > 50000:
        raise ValueError("Body exceeds limit")
    if (
        len(spec.add_labels) > 30
        or len(spec.remove_labels) > 30
        or (spec.assignees and len(spec.assignees) > 10)
    ):
        raise ValueError("Too many labels or assignees")
    if set(spec.add_labels).intersection(spec.remove_labels):
        raise ValueError("Cannot add and remove the same label")
    if len(spec.dependencies or ()) > 30 or any(
        number <= 0 or number == spec.issue_number for number in spec.dependencies or ()
    ):
        raise ValueError("Invalid dependencies")
    if spec.due_at:
        from datetime import datetime

        parsed = datetime.fromisoformat(spec.due_at)
        if parsed.tzinfo is None:
            raise ValueError("Due date requires timezone")


def marker(action_id: str) -> str:
    return f"<!-- peyda-action:{action_id} -->"
