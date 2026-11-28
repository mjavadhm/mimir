"""Typed inputs and immutable evidence shared by the issue workflow."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import Field

from mimir_pm_bot.application.memory import Positive, Request, Short, Text


@dataclass(frozen=True)
class Decision:
    subject: int
    revision: int
    text: str
    reason: str | None
    links: tuple[str, ...]


@dataclass(frozen=True)
class WorkSource:
    speaker: str | None
    speaker_id: int | None
    text: str
    link: str | None
    edited: bool


class IssueTracker(Protocol):
    repo: str

    async def create(self, title: str, body: str) -> int: ...
    async def reconcile(self, marker: str) -> int | None: ...
    async def metadata(self, number: int, pull: bool = False) -> dict[str, object]: ...


class Prepare(Request):
    decision: Short
    decision_revision: Positive
    expected_revision: int = Field(ge=0)
    title: Short
    description: Text


class Submit(Request):
    draft: Short
    revision: Positive
    source: Positive


class Status(Request):
    issue: Positive


class Start(Status):
    source: Positive


class Link(Start):
    pull: Positive


TOOLS: dict[str, tuple[type[Request], str]] = {
    "prepare_issue": (
        Prepare,
        "Create or revise an issue draft from a committed decision. "
        "expected_revision is zero initially. Returns the draft ID, revision and preview.",
    ),
    "submit_issue": (
        Submit,
        "Queue this exact draft after team agreement. "
        "source is the exact Telegram update ID that requested submission.",
    ),
    "start_work": (Start, "Record this issue's start for the speaker of source (update ID)."),
    "link_pull": (
        Link,
        "Associate a PR number from the bound repository with the issue. "
        "Use the exact update ID of the team's link as source.",
    ),
    "work_status": (
        Status,
        "Read issue assignees, current pull status and team start evidence, "
        "including other claims on the same subject.",
    ),
}
