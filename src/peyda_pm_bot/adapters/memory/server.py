"""Local stdio MCP gateway. No migration, recovery or I/O is run on import."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from peyda_pm_bot.adapters.storage.database import Database
from peyda_pm_bot.application.memory.service import MemoryService
from peyda_pm_bot.domain.memory.models import MemoryRecord


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["case", "note", "link", "summary", "architecture_draft"]
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=8000)
    id: str = Field(default="", max_length=200)
    category: Literal["proposal", "decision", "commitment", "question", "interpretation"] | None = (
        None
    )
    status: Literal["active", "inactive"] = "active"
    case_ids: list[str] = Field(default_factory=list, max_length=20)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    source_update_ids: list[int] = Field(default_factory=list, max_length=100)
    attributed_user_ids: list[int] = Field(default_factory=list, max_length=100)
    reason: str = Field(default="", max_length=2000)

    def to_record(self) -> MemoryRecord:
        return TypeAdapter(MemoryRecord).validate_python(self.model_dump())


def make_server(database: Database, project_id: str, workspace: Path | None = None) -> FastMCP:
    service = MemoryService(database.unit, project_id)

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await database.close()

    server = FastMCP(
        "peyda-memory",
        lifespan=lifespan,
        log_level="WARNING",
        instructions=(
            "Memory writes are staged, not committed, until the active turn succeeds. "
            "Use the turn_token supplied for the current job. Sources are exact update IDs. "
            "Use registered project tools for their specific operations."
        ),
    )

    @server.tool()
    async def memory_search(
        turn_token: str,
        query: str,
        cursor: str | None = None,
        limit: int = 10,
        evidence: bool = False,
    ) -> str:
        """Search cases, notes or source messages. Returns bounded pages and source references."""
        result = await service.search(
            turn_token, query, cursor=cursor, limit=limit, evidence=evidence
        )
        return json.dumps(asdict(result), ensure_ascii=False)

    @server.tool()
    async def memory_read(
        turn_token: str,
        record_id: str,
        history: bool = False,
        cursor: str | None = None,
        limit: int = 10,
    ) -> str:
        """Read a case with linked records or revision history, including staged writes."""
        result = await service.read(
            turn_token, record_id, history=history, cursor=cursor, limit=limit
        )
        return json.dumps(asdict(result), ensure_ascii=False)

    @server.tool()
    async def memory_sources(
        turn_token: str, update_ids: list[int], cursor: str | None = None, limit: int = 10
    ) -> str:
        """Read exact original message versions, speakers and superseded-source markers."""
        result = await service.sources(turn_token, tuple(update_ids), cursor=cursor, limit=limit)
        return json.dumps(asdict(result), ensure_ascii=False)

    @server.tool()
    async def memory_prepare(
        turn_token: str, operation_key: str, record: RecordInput, expected_revision: int = 0
    ) -> str:
        """Stage create/update/link/summary/draft or status=inactive with a reason. Not final yet.

        Retry with the same operation_key and identical input. For updates supply the current
        record ID and expected_revision. Decisions need sources; consent is the agent's policy.
        """
        result = await service.prepare(
            turn_token, operation_key, record.to_record(), expected_revision
        )
        return json.dumps(
            {"status": "staged_until_successful_turn", "view": asdict(result)}, ensure_ascii=False
        )

    if workspace is not None:
        from peyda_pm_bot.adapters.antigravity.project_files import register_project_files

        register_project_files(server, database.unit, project_id, workspace.resolve())
    return server


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Project-bound memory MCP server")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        raise SystemExit("Initialize the application database before starting memory tools")
    # Do not call Database.initialize: only the main application may migrate/recover.
    make_server(Database(args.database.resolve()), args.project, args.workspace).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
