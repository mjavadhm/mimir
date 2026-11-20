"""GitHub projection; local scheduling metadata is not a fake GitHub field."""

import builtins
import json
import time

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from peyda_pm_bot.adapters.storage.schema import work_items, work_scan, work_schedule
from peyda_pm_bot.domain.actions.models import ActionSpec, RemoteIssue
from peyda_pm_bot.domain.work.models import ScheduleState, WorkLink, WorkScan, WorkView, work_status

codec = TypeAdapter(RemoteIssue)
scan_codec = TypeAdapter(WorkScan)
fingerprints_codec = TypeAdapter(dict[str, str])


class SqlWork:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def observe(self, issue: RemoteIssue, spec: ActionSpec | None = None) -> None:
        old = (
            (
                await self.connection.execute(
                    select(work_items).where(work_items.c.issue_number == issue.number)
                )
            )
            .mappings()
            .first()
        )
        if old and codec.validate_json(old["snapshot"]).updated_at > issue.updated_at:
            return
        metadata = json.loads(old["metadata"]) if old else {}
        cases = json.loads(old["case_ids"]) if old else []
        if spec:
            cases = list(spec.case_ids) or cases
            if spec.due_at is not None:
                metadata["due_at"] = spec.due_at or None
            if spec.dependencies is not None:
                metadata["dependencies"] = list(spec.dependencies)
            if spec.priority is not None:
                metadata["priority"] = spec.priority or None

        values = dict(
            snapshot=codec.dump_json(issue).decode(),
            observed_at=time.time(),
            case_ids=json.dumps(cases),
            metadata=json.dumps(metadata),
        )
        await self.connection.execute(
            insert(work_items)
            .values(issue_number=issue.number, **values)
            .on_conflict_do_update(index_elements=["issue_number"], set_=values)
        )

    async def list(self, status_labels: dict[str, str]) -> list[WorkView]:
        result = []
        for row in (
            await self.connection.execute(select(work_items).order_by(work_items.c.issue_number))
        ).mappings():
            issue = codec.validate_json(row["snapshot"])
            status, conflicts = work_status(issue, status_labels)
            metadata = json.loads(row["metadata"])
            result.append(
                WorkView(
                    issue,
                    status,
                    conflicts,
                    tuple(json.loads(row["case_ids"])),
                    metadata.get("due_at"),
                    tuple(metadata.get("dependencies", [])),
                    metadata.get("priority"),
                    row["observed_at"],
                )
            )
        return result

    async def links(self, case_id: str) -> builtins.list[WorkLink]:
        result = []
        for row in (await self.connection.execute(select(work_items))).mappings():
            if case_id in json.loads(row["case_ids"]):
                issue = codec.validate_json(row["snapshot"])
                result.append(WorkLink(issue.number, issue.url, issue.state, row["observed_at"]))
        return result

    async def scan(self) -> WorkScan:
        value = await self.connection.scalar(select(work_scan.c.snapshot))
        return scan_codec.validate_json(value) if value is not None else WorkScan()

    async def save_scan(self, scan: WorkScan) -> None:
        snapshot = scan_codec.dump_json(scan).decode()
        await self.connection.execute(
            insert(work_scan)
            .values(id=1, snapshot=snapshot)
            .on_conflict_do_update(index_elements=["id"], set_=dict(snapshot=snapshot))
        )

    async def schedule(self, name: str) -> ScheduleState | None:
        row = (
            await self.connection.execute(select(work_schedule).where(work_schedule.c.name == name))
        ).first()
        if row is None:
            return None
        return ScheduleState(row.local_day, fingerprints_codec.validate_json(row.fingerprints))

    async def save_schedule(self, name: str, state: ScheduleState) -> None:
        values = dict(local_day=state.local_day, fingerprints=json.dumps(state.fingerprints))
        await self.connection.execute(
            insert(work_schedule)
            .values(name=name, **values)
            .on_conflict_do_update(index_elements=["name"], set_=values)
        )
