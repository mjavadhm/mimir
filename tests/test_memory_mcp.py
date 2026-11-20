"""Real local MCP client/server exchange; no agent or Telegram login needed."""

import json
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select
from test_memory import begin, finalize

from peyda_pm_bot.adapters.storage.schema import outbox
from peyda_pm_bot.domain.agent.models import AgentResult


async def test_mcp_tools_scope_staging_and_no_recovery(database, conversations):
    request, token, service = await begin(database, conversations)
    async with database.unit() as unit:
        await unit.connection.execute(
            outbox.insert().values(
                chat_id=-1001, topic_id=42, reply_to_id=100, text="in-flight", status="sending"
            )
        )
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "peyda_pm_bot.adapters.memory.server",
            "--database",
            database.engine.url.database,
            "--project",
            "test",
        ],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=10)
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "memory_search",
                "memory_read",
                "memory_sources",
                "memory_prepare",
            }
            denied = await session.call_tool(
                "memory_search", {"turn_token": "fake", "query": "ورود"}
            )
            assert denied.isError
            prepared = await session.call_tool(
                "memory_prepare",
                {
                    "turn_token": token,
                    "operation_key": "case",
                    "record": {"kind": "case", "title": "ورود", "source_update_ids": [10]},
                },
            )
            assert not prepared.isError
            payload = json.loads(prepared.content[0].text)
            assert payload["status"] == "staged_until_successful_turn"
            case_id = payload["view"]["record"]["id"]
            note = await session.call_tool(
                "memory_prepare",
                {
                    "turn_token": token,
                    "operation_key": "note",
                    "record": {
                        "kind": "note",
                        "title": "تصمیم روشن",
                        "text": "ورود با شماره",
                        "category": "decision",
                        "case_ids": [case_id],
                        "source_update_ids": [10],
                        "attributed_user_ids": [7],
                    },
                },
            )
            assert not note.isError
            found = await session.call_tool(
                "memory_read", {"turn_token": token, "record_id": case_id}
            )
            assert len(json.loads(found.content[0].text)["items"]) == 2
            evidence = await session.call_tool(
                "memory_sources", {"turn_token": token, "update_ids": [10]}
            )
            assert json.loads(evidence.content[0].text)["items"][0]["speaker_id"] == 7
            async with database.unit() as unit:
                assert not await unit.memory.views()
                # Tool-server startup must not run the application's outbox recovery.
                assert (
                    await unit.connection.scalar(
                        select(outbox.c.status).where(outbox.c.text == "in-flight")
                    )
                    == "sending"
                )
            await finalize(database, request, AgentResult("silent"))
            late = await session.call_tool("memory_search", {"turn_token": token, "query": "ورود"})
            assert late.isError
