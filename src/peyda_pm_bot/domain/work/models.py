"""Read-only work projections and configurable status-label mapping."""

from dataclasses import dataclass

from peyda_pm_bot.domain.actions.models import ActionSpec, RemoteIssue

STATUSES = ("backlog", "ready", "in_progress", "in_review", "blocked", "done")


@dataclass(frozen=True)
class WorkView:
    issue: RemoteIssue
    status: str
    conflicts: tuple[str, ...] = ()
    case_ids: tuple[str, ...] = ()
    due_at: str | None = None
    dependencies: tuple[int, ...] = ()
    priority: str | None = None
    observed_at: float = 0


def work_status(issue: RemoteIssue, labels: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    states = [state for state, label in labels.items() if label in issue.labels]
    conflicts = []
    if len(states) > 1:
        conflicts.append("Multiple status labels")
    if issue.state == "closed":
        if any(state != "done" for state in states):
            conflicts.append("Closed issue has a non-done status label")
        return "done", tuple(conflicts)
    if "done" in states:
        conflicts.append("Open issue labelled done")
    return (states[0] if len(states) == 1 and states[0] != "done" else "backlog"), tuple(conflicts)


def desired_fields(
    spec: ActionSpec, current: RemoteIssue, status_labels: dict[str, str]
) -> dict[str, object]:
    fields: dict[str, object] = {}
    if spec.title is not None:
        fields["title"] = spec.title
    if spec.body is not None:
        fields["body"] = spec.body
    if spec.assignees is not None:
        fields["assignees"] = list(spec.assignees)
    labels = set(current.labels)
    labels.update(spec.add_labels)
    labels.difference_update(spec.remove_labels)
    if spec.work_status:
        labels.difference_update(status_labels.values())
        labels.add(status_labels[spec.work_status])
    if spec.add_labels or spec.remove_labels or spec.work_status:
        fields["labels"] = sorted(labels)
    if spec.state is not None:
        fields["state"] = spec.state
    if spec.work_status == "done":
        fields["state"] = "closed"
    elif spec.work_status:
        fields["state"] = "open"
    return fields


@dataclass(frozen=True)
class WorkLink:
    issue_number: int
    url: str
    state: str
    observed_at: float


@dataclass(frozen=True)
class PullSummary:
    number: int
    title: str
    url: str
    state: str
    draft: bool
    updated_at: str


@dataclass(frozen=True)
class BranchProtection:
    protected: bool | None = None
    pull_requests_required: bool | None = None
    checks_required: bool | None = None


@dataclass(frozen=True)
class WorkScan:
    next_attempt_at: float = 0
    observed_at: float | None = None
    complete: bool = False
    issue_numbers: tuple[int, ...] | None = None
    pulls: tuple[PullSummary, ...] = ()
    protection: BranchProtection = BranchProtection()


@dataclass(frozen=True)
class ScheduleState:
    local_day: str
    fingerprints: dict[str, str]
