"""Build reports and follow-up notices from recorded evidence."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from peyda_pm_bot.domain.memory.models import MemoryView
from peyda_pm_bot.domain.work.models import BranchProtection, WorkScan, WorkView

MAX_TEXT = 2000  # Fits the Telegram limit even when all characters use surrogate pairs.


@dataclass(frozen=True)
class WorkReport:
    text: str
    fingerprint: str


@dataclass(frozen=True)
class Reminder:
    issue_number: int
    text: str
    fingerprint: str


def fingerprint(data: object) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def single_line(text: str, limit: int = 125) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def render_sections(
    header: str, sections: list[tuple[str, list[str]]], *, per_section: int | None = 3
) -> str:
    truncated = "\n… برای بقیهٔ موارد به مخزن و حافظه مراجعه کنید."
    headings = [f"{name} ({len(lines)})" for name, lines in sections]
    size = len(header) + sum(len(heading) + 1 for heading in headings)
    budget = MAX_TEXT - size - len(truncated)
    paragraphs = [header]
    omitted = False
    for heading, (_, lines) in zip(headings, sections, strict=True):
        paragraphs.append(heading)
        for line in lines[:per_section]:
            if len(line) + 1 > budget:
                omitted = True
                break
            paragraphs.append(line)
            budget -= len(line) + 1
        omitted |= per_section is not None and len(lines) > per_section
    return "\n".join(paragraphs) + (truncated if omitted else "")


def observation_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        observed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return observed.astimezone(UTC) if observed.tzinfo is not None else None


def open_dependencies(view: WorkView, issues: dict[int, WorkView]) -> tuple[int, ...]:
    return tuple(
        sorted(
            number
            for number in view.dependencies
            if number in issues and issues[number].status != "done"
        )
    )


def issue_line(view: WorkView, issues: dict[int, WorkView], timezone: tzinfo) -> str:
    issue = view.issue
    title = single_line(issue.title, 80)
    assignees = single_line("، ".join(issue.assignees) or "بدون مسئول", 56)
    fields = [f"#{issue.number} {title}", single_line(issue.url, 240), assignees]
    if due := observation_time(view.due_at):
        fields.append(f"موعد {due.astimezone(timezone):%Y-%m-%d %H:%M}")
    if dependencies := open_dependencies(view, issues):
        fields.append("پیگیری وابستگی " + "، ".join(f"#{number}" for number in dependencies))
    if any(number not in issues for number in view.dependencies):
        fields.append("وضعیت بعضی وابستگی‌ها نامشخص")
    if view.conflicts:
        fields.append("بررسی وضعیت و برچسب‌ها")
    elif view.status == "blocked":
        fields.append("رفع مانع")
    elif not issue.assignees and view.status != "done":
        fields.append("تعیین مسئول")
    elif view.status == "in_review":
        fields.append("پیگیری بررسی")
    return " | ".join(fields)


def protection_summary(protection: BranchProtection) -> str:
    values = {True: "بله", False: "خیر", None: "نامشخص"}
    return (
        f"main محافظت‌شده: {values[protection.protected]}"
        f" | PR الزامی: {values[protection.pull_requests_required]}"
        f" | بررسی CI الزامی: {values[protection.checks_required]}"
    )


def report(
    views: list[WorkView],
    memory: list[MemoryView],
    scan: WorkScan,
    now: datetime,
    timezone: tzinfo,
    fresh_for: timedelta,
) -> WorkReport:
    issues = {view.issue.number: view for view in views}
    buckets: dict[str, tuple[str, list[str]]] = {
        "done": ("تکمیل‌شده", []),
        "in_progress": ("در جریان", []),
        "in_review": ("منتظر بررسی", []),
        "blocked": ("مسدود", []),
    }
    unassigned: list[str] = []
    for view in sorted(views, key=lambda item: item.issue.updated_at, reverse=True):
        line = issue_line(view, issues, timezone)
        if view.status in buckets:
            buckets[view.status][1].append(line)
        if view.status != "done" and not view.issue.assignees:
            unassigned.append(line)
    opened_pulls = [
        f"#{pull.number} {single_line(pull.title)} | {single_line(pull.url, 240)}"
        + (" | پیش‌نویس" if pull.draft else " | بررسی انسانی")
        for pull in sorted(scan.pulls, key=lambda item: item.number, reverse=True)
        if pull.state == "open"
    ]
    sections = [
        *buckets.values(),
        ("بدون مسئول", unassigned),
        ("PRهای باز", opened_pulls),
        ("منتظر پاسخ", memory_titles(memory, "question")),
        ("تعهدهای ثبت‌شده", memory_titles(memory, "commitment")),
    ]
    protection = protection_summary(scan.protection)
    header = f"گزارش پروژه\n{scan_status(scan, now, timezone, fresh_for)}\n{protection}"
    # Timestamps and display ordering alone do not make the report new.
    identity = [(name, sorted(lines)) for name, lines in sections]
    return WorkReport(
        render_sections(header, sections), fingerprint((identity, scan.complete, protection))
    )


def memory_titles(memory: list[MemoryView], category: str) -> list[str]:
    return sorted(
        single_line(view.record.title) + (" | نیازمند بازبینی منبع" if view.needs_review else "")
        for view in memory
        if view.record.category == category and view.record.status == "active"
    )


def scan_status(scan: WorkScan, now: datetime, timezone: tzinfo, fresh_for: timedelta) -> str:
    if scan.observed_at is None:
        return "وضعیت GitHub هنوز مشاهده نشده است."
    seen = datetime.fromtimestamp(scan.observed_at, UTC).astimezone(timezone)
    qualifiers = [] if scan.complete else ["ناقص"]
    if now - seen > fresh_for:
        qualifiers.append("قدیمی")
    suffix = " (" + "، ".join(qualifiers) + ")" if qualifiers else ""
    return f"آخرین مشاهدهٔ GitHub: {seen:%Y-%m-%d %H:%M}{suffix}"


def reminders(
    views: list[WorkView], now: datetime, timezone: tzinfo, idle_for: timedelta
) -> list[Reminder]:
    issues = {view.issue.number: view for view in views}
    result = []
    for view in views:
        if view.status == "done":
            continue
        due = observation_time(view.due_at)
        updated = observation_time(view.issue.updated_at)
        reasons = []
        if due is not None and due <= now:
            reasons.append(f"گذشتن از موعد {due.astimezone(timezone):%Y-%m-%d %H:%M}")
        if view.status == "blocked":
            reasons.append("مانع باز")
        if dependencies := open_dependencies(view, issues):
            numbers = "، ".join(f"#{number}" for number in dependencies)
            reasons.append(f"وابستگی باز {numbers}")
        if view.status == "in_progress" and updated is not None and now - updated >= idle_for:
            reasons.append("بدون فعالیت تازه")
        if reasons:
            text = f"{issue_line(view, issues, timezone)} | " + "، ".join(reasons)
            identity = (view.status, view.issue.assignees, reasons)
            result.append(Reminder(view.issue.number, text, fingerprint(identity)))
    return result
