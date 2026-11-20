"""Exercise the integrated launcher and the turn-bound project tool."""

import json
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from acp import schema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_memory import begin

from peyda_pm_bot.adapters.antigravity import setup
from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
from peyda_pm_bot.adapters.antigravity.project_files import create_text_file
from peyda_pm_bot.adapters.github import http
from peyda_pm_bot.adapters.github.fake import FakeGithub
from peyda_pm_bot.adapters.telegram import diagnostics
from peyda_pm_bot.bootstrap import telegram_command
from peyda_pm_bot.runtime import runner
from peyda_pm_bot.runtime.process import ProcessOwner


async def test_serve_wires_all_workers_and_keeps_smoke_separate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "PROJECT_ID": "test",
        "GROUP_ID": "-1001",
        "TOPIC_ID": "42",
        "ADMIN_IDS": "[7]",
        "DATABASE_PATH": str(tmp_path / "product.db"),
        "BOT_TOKEN": "123456:FAKE_OFFLINE_TOKEN",
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_TOKEN": "fake",
        "GITHUB_BOT_LOGIN": "pm-bot",
        "GITHUB_LIVE_VALIDATED": "true",
    }.items():
        monkeypatch.setenv("PEYDA_" + key, value)
    monkeypatch.setattr(
        diagnostics,
        "inspect_bot",
        AsyncMock(return_value=diagnostics.BotStatus(123456, "fake", False, True, True, "member")),
    )
    agent_factory = AsyncMock(side_effect=lambda *args, **kwargs: FakeAgent())
    monkeypatch.setattr(setup, "live_agent", agent_factory)
    monkeypatch.setattr(http, "HttpGithub", lambda *args, **kwargs: FakeGithub())
    run = AsyncMock()
    monkeypatch.setattr(runner, "run", run)

    await telegram_command(serve=True)
    assert agent_factory.await_args.kwargs["http_mcp"]
    assert agent_factory.await_args.kwargs["project_tools"]
    assert run.await_args.kwargs["action_executor"] is not None
    assert run.await_args.kwargs["followup"] is not None

    await telegram_command(agent_enabled=True)
    assert not agent_factory.await_args.kwargs["http_mcp"]
    assert not agent_factory.await_args.kwargs["project_tools"]
    assert run.await_args.kwargs["action_executor"] is None
    assert run.await_args.kwargs["followup"] is None


async def test_turbo_only_approves_registered_operations_in_the_active_session(tmp_path):
    agent = AcpAgent(ProcessOwner(["unused"], tmp_path), tmp_path, project_tools=True)
    agent.session_id, agent.in_turn = "current", True
    agent.mcp_servers = [
        schema.McpServerStdio(name="peyda-memory", command="unused", args=[], env=[])
    ]
    options = [
        schema.PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
        schema.PermissionOption(option_id="deny", name="Deny", kind="reject_once"),
    ]

    async def outcome(session, name):
        tool = schema.ToolCallUpdate(tool_call_id="t", name=name, kind="other")
        return (await agent.request_permission(session, tool, options)).outcome.option_id

    name = "mcp_peyda-memory_project_create_file"
    assert await outcome("current", name) == "deny"
    assert await agent.set_turbo(True)
    assert await outcome("current", name) == "allow"
    assert await outcome("stale", name) == "deny"
    assert await outcome("current", "run_command") == "deny"
    assert await agent.set_turbo(False)
    assert await outcome("current", name) == "deny"


def test_create_does_not_escape_or_replace_paths(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "symlink").symlink_to(outside, target_is_directory=True)
    (project / "target.txt").symlink_to(outside / "target.txt")
    for path in ("../outside/new.txt", ".env", str(outside / "new.txt")):
        with pytest.raises(ValueError):
            create_text_file(project, path, "not written")
    for path in ("symlink/new.txt", "target.txt"):
        with pytest.raises(OSError):
            create_text_file(project, path, "not written")
    assert list(outside.iterdir()) == []
    assert create_text_file(project, "daily.txt", "سلام") == "daily.txt"
    with pytest.raises(FileExistsError):
        create_text_file(project, "daily.txt", "replacement")
    assert (project / "daily.txt").read_text() == "سلام"
    assert not list(project.glob(".peyda-new-*"))


async def test_live_stdio_file_tool_requires_current_grant_and_effective_turbo(
    database, conversations, tmp_path: Path
):
    request, token, _ = await begin(database, conversations)
    root = tmp_path / "workspace"
    root.mkdir()
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "peyda_pm_bot.adapters.memory.server",
            "--database",
            database.engine.url.database,
            "--project",
            "test",
            "--workspace",
            str(root),
        ],
    )
    inputs = {"turn_token": token, "path": "notes.txt", "content": "new notes"}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as client:
            await client.initialize()
            assert (await client.call_tool("project_create_file", inputs)).isError
            async with database.unit() as unit:
                await unit.queue.turbo_applied(True)
            result = await client.call_tool("project_create_file", inputs)
            assert not result.isError and json.loads(result.content[0].text)["status"] == "created"
            assert (root / "notes.txt").read_text() == "new notes"
            async with database.unit() as unit:
                await unit.queue.control("/cancel", str(request.job_id))
            assert (
                await client.call_tool("project_create_file", {**inputs, "path": "late.txt"})
            ).isError
            assert not (root / "late.txt").exists()
