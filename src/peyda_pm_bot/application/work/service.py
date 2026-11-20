"""Refresh recorded work and publish due notices through the outbox."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from datetime import time as LocalTime
from zoneinfo import ZoneInfo

from peyda_pm_bot.application.actions.errors import GithubPermanent, GithubTemporary
from peyda_pm_bot.application.actions.ports import Github
from peyda_pm_bot.application.ports import UnitOfWork, UowFactory
from peyda_pm_bot.domain.work.models import BranchProtection, ScheduleState, WorkScan, WorkView
from peyda_pm_bot.domain.work.report import WorkReport, reminders, render_sections, report

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkSchedule:
    timezone: ZoneInfo
    report_at: LocalTime | None = None
    reminder_at: LocalTime | None = None
    sync_seconds: float = 900
    idle_hours: float = 72

    @property
    def fresh_for(self) -> timedelta:
        return timedelta(seconds=self.sync_seconds * 2)


class WorkSync:
    def __init__(
        self,
        uow: UowFactory,
        github: Github,
        interval: float,
        clock: Callable[[], float],
    ) -> None:
        self.uow = uow
        self.github = github
        self.interval = interval
        self.clock = clock
        self.lock = asyncio.Lock()

    async def refresh(self) -> None:
        async with self.lock:
            await self._refresh()

    async def _refresh(self) -> None:
        now = self.clock()
        async with self.uow() as unit:
            previous = await unit.work.scan()
        if previous.next_attempt_at > now:
            return
        try:
            async with asyncio.timeout(120):
                issues, issues_complete = await self.github.issues()
                pulls, pulls_complete = await self.github.pull_summaries()
        except GithubTemporary as error:
            await self._failed(previous, max(min(60, self.interval), error.retry_after))
            return
        except (GithubPermanent, ValueError, TimeoutError):
            await self._failed(previous, self.interval)
            return

        try:
            async with asyncio.timeout(30):
                protection = await self.github.branch_protection()
        except (GithubPermanent, GithubTemporary, ValueError, TimeoutError):
            protection = BranchProtection()
        issues = [issue for issue in issues if not issue.is_pull_request]
        issue_numbers = {issue.number for issue in issues}
        if not issues_complete and previous.issue_numbers is not None:
            issue_numbers.update(previous.issue_numbers)
        pull_by_number = {} if pulls_complete else {item.number: item for item in previous.pulls}
        for pull in pulls:
            existing = pull_by_number.get(pull.number)
            if existing is None or pull.updated_at >= existing.updated_at:
                pull_by_number[pull.number] = pull
        scan = WorkScan(
            self.clock() + self.interval,
            now,
            issues_complete and pulls_complete,
            tuple(sorted(issue_numbers))
            if issues_complete or previous.issue_numbers is not None
            else None,
            tuple(pull_by_number.values()),
            protection,
        )
        async with self.uow() as unit:
            for issue in issues:
                await unit.work.observe(issue)
            await unit.work.save_scan(scan)

    async def _failed(self, previous: WorkScan, retry_after: float) -> None:
        logger.warning("GitHub work refresh unavailable")
        async with self.uow() as unit:
            await unit.work.save_scan(
                replace(previous, complete=False, next_attempt_at=self.clock() + retry_after)
            )


class WorkFollowup:
    def __init__(
        self,
        uow: UowFactory,
        status_labels: dict[str, str],
        destination: tuple[int, int],
        schedule: WorkSchedule,
        github: Github | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.uow = uow
        self.status_labels = status_labels
        self.group_id, self.topic_id = destination
        self.schedule = schedule
        self.clock = clock
        self.sync = WorkSync(uow, github, schedule.sync_seconds, clock) if github else None

    async def _views(self, unit: UnitOfWork, scan: WorkScan) -> list[WorkView]:
        views = await unit.work.list(self.status_labels)
        if scan.issue_numbers is None or scan.observed_at is None:
            return views
        numbers = set(scan.issue_numbers)
        # Actions and direct reads can observe an issue that was absent from the last scan.
        return [
            view
            for view in views
            if view.issue.number in numbers or view.observed_at > scan.observed_at
        ]

    async def _report(
        self, unit: UnitOfWork, scan: WorkScan, views: list[WorkView], now: datetime
    ) -> WorkReport:
        return report(
            views,
            await unit.memory.views(),
            scan,
            now,
            self.schedule.timezone,
            self.schedule.fresh_for,
        )

    async def report_text(self, unit: UnitOfWork) -> str:
        scan = await unit.work.scan()
        views = await self._views(unit, scan)
        now = datetime.fromtimestamp(self.clock(), UTC)
        return (await self._report(unit, scan, views, now)).text

    async def run_once(self) -> None:
        if self.sync is not None:
            await self.sync.refresh()
        if self.schedule.report_at is None and self.schedule.reminder_at is None:
            return
        now = datetime.fromtimestamp(self.clock(), UTC)
        async with self.uow() as unit:
            scan = await unit.work.scan()
            fresh = (
                scan.complete
                and scan.observed_at is not None
                and now.timestamp() - scan.observed_at <= self.schedule.fresh_for.total_seconds()
            )
            if self.sync is not None and not fresh:
                return
            views = await self._views(unit, scan)
            if self.schedule.report_at is not None:
                prior = await unit.work.schedule("report")
                if slot := self._due_slot(now, self.schedule.report_at, prior):
                    value = await self._report(unit, scan, views, now)
                    await self._publish(
                        unit, "report", slot, prior, {"report": value.fingerprint}, value.text
                    )
            if self.schedule.reminder_at is not None and fresh:
                prior = await unit.work.schedule("reminder")
                if slot := self._due_slot(now, self.schedule.reminder_at, prior):
                    await self._remind(unit, views, now, slot, prior)

    def _due_slot(self, now: datetime, at: LocalTime, prior: ScheduleState | None) -> str | None:
        local = now.astimezone(self.schedule.timezone)
        day = local.date()
        if local.time() < at:
            if prior is None:
                return None
            day -= timedelta(days=1)
        value = day.isoformat()
        return value if prior is None or value > prior.local_day else None

    async def _remind(
        self,
        unit: UnitOfWork,
        views: list[WorkView],
        now: datetime,
        slot: str,
        prior: ScheduleState | None,
    ) -> None:
        candidates = reminders(
            views, now, self.schedule.timezone, timedelta(hours=self.schedule.idle_hours)
        )
        current = {str(item.issue_number): item.fingerprint for item in candidates}
        previous = prior.fingerprints if prior else {}
        changed = [
            item.text
            for item in candidates
            if item.fingerprint != previous.get(str(item.issue_number))
        ]
        text = (
            render_sections("پیگیری پروژه", [("اقدام‌های لازم", changed)], per_section=None)
            if changed
            else ""
        )
        await self._publish(unit, "reminder", slot, prior, current, text)

    async def _publish(
        self,
        unit: UnitOfWork,
        name: str,
        slot: str,
        prior: ScheduleState | None,
        fingerprints: dict[str, str],
        text: str,
    ) -> None:
        if text and (prior is None or fingerprints != prior.fingerprints):
            await unit.outgoing.scheduled_notice(
                f"work:{name}:{slot}", self.group_id, self.topic_id, text
            )
        await unit.work.save_schedule(name, ScheduleState(slot, fingerprints))
