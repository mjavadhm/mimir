"""Authenticated memory use cases. Team consent remains a textual policy."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from peyda_pm_bot.application.agent.turns import authorize_turn
from peyda_pm_bot.application.ports import UnitOfWork, UowFactory
from peyda_pm_bot.domain.memory.models import (
    Evidence,
    MemoryRecord,
    MemoryTurn,
    MemoryView,
    normalize,
)
from peyda_pm_bot.domain.work.models import WorkLink


@dataclass(frozen=True)
class MemoryPage:
    items: tuple[MemoryView | Evidence | WorkLink, ...]
    next_cursor: str | None = None


def page(
    items: Sequence[MemoryView | Evidence | WorkLink], cursor: str | None, limit: int, scope: str
) -> MemoryPage:
    if not 1 <= limit <= 20:
        raise ValueError("Page size must be between one and twenty")
    fingerprint = hashlib.sha256(
        (
            scope + json.dumps([asdict(item) for item in items], sort_keys=True, ensure_ascii=False)
        ).encode()
    ).hexdigest()[:20]
    offset = 0
    if cursor:
        prefix, separator, value = cursor.partition(":")
        if not separator or prefix != fingerprint or not value.isdecimal():
            raise ValueError("Invalid or stale cursor")
        offset = int(value)
        if offset > len(items):
            raise ValueError("Invalid cursor offset")
    selected: list[MemoryView | Evidence | WorkLink] = []
    for item in items[offset : offset + limit]:
        candidate = MemoryPage(
            tuple([*selected, item]), f"{fingerprint}:{offset + len(selected) + 1}"
        )
        if len(json.dumps(asdict(candidate), ensure_ascii=False)) > 12000:
            if not selected:
                raise ValueError(
                    "Single item exceeds response budget; request source evidence separately"
                )
            break
        selected.append(item)
    end = offset + len(selected)
    return MemoryPage(tuple(selected), f"{fingerprint}:{end}" if end < len(items) else None)


class MemoryService:
    def __init__(self, uow: UowFactory, project_id: str) -> None:
        self.uow = uow
        self.project_id = project_id

    async def _authorize(self, unit: UnitOfWork, token: str) -> MemoryTurn:
        return await authorize_turn(unit, self.project_id, token)

    async def search(
        self,
        token: str,
        query: str,
        *,
        cursor: str | None = None,
        limit: int = 10,
        evidence: bool = False,
    ) -> MemoryPage:
        if not query.strip() or len(query) > 200:
            raise ValueError("Search requires a bounded query")
        async with self.uow() as unit:
            turn = await self._authorize(unit, token)
            value = normalize(query)
            if evidence:
                items = await unit.incoming.search_evidence(value)
                return page(items, cursor, limit, f"evidence:{value}")
            views = await unit.memory.views(turn)
            matches = []
            for view in views:
                record = view.record
                if record.status != "active":
                    continue
                aliases = {normalize(item) for item in record.aliases}
                rank = (
                    0
                    if value == normalize(record.id) or value in aliases
                    else 1
                    if value in normalize(record.title)
                    else 2
                )
                if rank < 2 or value in normalize(record.text):
                    matches.append((rank, view.updated_at, record.id, view))
            matches.sort(key=lambda item: item[2])
            matches.sort(key=lambda item: item[1], reverse=True)
            matches.sort(key=lambda item: item[0])
            return page([item[3] for item in matches], cursor, limit, f"search:{value}")

    async def read(
        self,
        token: str,
        record_id: str,
        *,
        history: bool = False,
        cursor: str | None = None,
        limit: int = 10,
    ) -> MemoryPage:
        async with self.uow() as unit:
            turn = await self._authorize(unit, token)
            if history:
                views = await unit.memory.history(record_id)
            else:
                all_views = await unit.memory.views(turn)
                views = sorted(
                    [
                        view
                        for view in all_views
                        if view.record.id == record_id or record_id in view.record.case_ids
                    ],
                    key=lambda view: (view.record.id != record_id, view.record.id),
                )
            items: list[MemoryView | Evidence | WorkLink] = list(views)
            if not history:
                items.extend(await unit.work.links(record_id))
            return page(items, cursor, limit, f"read:{record_id}:{history}")

    async def sources(
        self, token: str, update_ids: tuple[int, ...], *, cursor: str | None = None, limit: int = 10
    ) -> MemoryPage:
        if not update_ids or len(update_ids) > 100:
            raise ValueError("Provide between one and one hundred source versions")
        async with self.uow() as unit:
            await self._authorize(unit, token)
            evidence = await unit.memory.evidence(update_ids)
            if set(update_ids) != {item.update_id for item in evidence}:
                raise ValueError("Unknown source version")
            return page(evidence, cursor, limit, f"sources:{sorted(update_ids)}")

    async def prepare(
        self, token: str, operation_key: str, record: MemoryRecord, expected_revision: int = 0
    ) -> MemoryView:
        async with self.uow() as unit:
            turn = await self._authorize(unit, token)
            return await unit.memory.stage(turn, operation_key, record, expected_revision)
