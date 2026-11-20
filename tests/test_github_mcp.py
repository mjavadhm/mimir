"""Loopback MCP tool execution shares the grant, not the GitHub credential."""

import json
from datetime import timedelta

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_actions import prepare, publish

from peyda_pm_bot.adapters.github.hosting import host_server
from peyda_pm_bot.adapters.github.server import make_server
from peyda_pm_bot.application.actions.executor import ActionExecutor


async def test_loopback_tools_and_shared_grant(database, conversations):
    request, token, memory, github, service, draft = await prepare(database, conversations)
    async with host_server(make_server(service)) as url:
        assert url.startswith("http://127.0.0.1:")
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=10)
            ) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert names == {
                    "github_read",
                    "github_prepare",
                    "github_submit",
                    "github_action",
                    "work_status",
                }
                assert "token" not in json.dumps(
                    [tool.model_dump() for tool in (await session.list_tools()).tools]
                ).replace("turn_token", "")
                denied = await session.call_tool(
                    "github_read", {"turn_token": "invalid", "resource": "issues"}
                )
                assert denied.isError
                unknown_scope = await session.call_tool(
                    "github_prepare",
                    {
                        "turn_token": token,
                        "operation_key": "nope",
                        "spec": {
                            "kind": "create_issue",
                            "title": "X",
                            "body": "B",
                            "source_update_ids": [10],
                            "repository": "other/repo",
                        },
                    },
                )
                assert unknown_scope.isError
                submitted = await session.call_tool(
                    "github_submit",
                    {"turn_token": token, "action_id": draft.id, "revision": draft.revision},
                )
                assert not submitted.isError
                assert github.mutations == 0
                await publish(database, request)
                late = await session.call_tool(
                    "github_read", {"turn_token": token, "resource": "issues"}
                )
                assert late.isError
        await ActionExecutor(database.unit, github).run_one()
        assert github.mutations == 1


async def test_acp_rejects_unadvertised_http_mcp_without_fallback(tmp_path):
    import sys
    from pathlib import Path

    from acp import schema

    from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
    from peyda_pm_bot.runtime.process import ProcessOwner

    script = Path(__file__).parent / "fixtures" / "acp_server.py"
    agent = AcpAgent(
        ProcessOwner([sys.executable, str(script)], tmp_path), tmp_path, validated_environment=True
    )
    agent.mcp_servers = [
        schema.HttpMcpServer(name="github", url="http://127.0.0.1:1/mcp", headers=[], type="http")
    ]
    try:
        with pytest.raises(RuntimeError, match="not advertised"):
            await agent.connect(None)
    finally:
        await agent.close()


async def test_acp_accepts_http_for_exact_live_verified_release(tmp_path):
    import sys
    from pathlib import Path

    from acp import schema

    from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
    from peyda_pm_bot.runtime.process import ProcessOwner

    script = Path(__file__).parent / "fixtures/acp_server.py"
    agent = AcpAgent(
        ProcessOwner(
            [sys.executable, str(script), "--antigravity-1.5.10"],
            tmp_path,
        ),
        tmp_path,
        validated_environment=True,
    )
    agent.mcp_servers = [
        schema.HttpMcpServer(
            name="peyda-github",
            url="http://127.0.0.1:1/mcp",
            headers=[],
            type="http",
        )
    ]
    try:
        assert await agent.connect(None) == "offline-acp-session"
    finally:
        await agent.close()
