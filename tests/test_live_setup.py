"""Live wiring is exercised offline; these tests never contact external services."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import ChatFullInfo, ChatMemberMember, Update, User, WebhookInfo
from conftest import event
from sqlalchemy import func, select

from peyda_pm_bot.adapters.storage.database import Database
from peyda_pm_bot.adapters.storage.schema import jobs, outbox
from peyda_pm_bot.adapters.telegram.aiogram_transport import AiogramTransport
from peyda_pm_bot.adapters.telegram.diagnostics import discover_topic, inspect_bot
from peyda_pm_bot.bootstrap import telegram_command
from peyda_pm_bot.config import Settings
from peyda_pm_bot.runtime.runner import shutdown_signals


async def test_live_cli_uses_project_root_and_normal_profile(tmp_path, monkeypatch):
    from peyda_pm_bot.adapters.antigravity.setup import AgentSetupError, live_agent

    binary = tmp_path / "agy-cli"
    binary.write_text("#!/bin/sh\nprintf '%s\\n' 1.5.10\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(AgentSetupError, match="PEYDA_AGENT_CONVERSATION_ID"):
        await live_agent(Path("agy-cli"), root, None)
    agent = await live_agent(Path("agy-cli"), root, "existing")
    assert agent.owner.cwd == agent.root == root
    assert agent.owner.command[0] == str(binary)
    assert "--gemini-dir" not in agent.owner.command
    assert not agent.owner.desktop_keyring
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("unavailable", [False, True])
async def test_bound_conversation_never_creates_another_session(tmp_path, unavailable):
    from acp import RequestError

    from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
    from peyda_pm_bot.runtime.process import ProcessOwner

    fixture = Path(__file__).parent / "fixtures/acp_server.py"
    owner = ProcessOwner(
        [sys.executable, str(fixture), "--load-only", "existing", str(tmp_path)],
        tmp_path,
    )
    if unavailable:
        owner.command.append("--missing-conversation")
    agent = AcpAgent(owner, tmp_path, validated_environment=True, conversation_id="existing")
    try:
        async with asyncio.timeout(10):
            if unavailable:
                with pytest.raises(RequestError, match="Conversation not found"):
                    await agent.connect("old-smoke-session")
            else:
                assert await agent.connect("old-smoke-session") == "existing"
                await agent.close()
                assert await agent.connect("existing") == "existing"
    finally:
        await agent.close()


async def test_doctor_checks_membership_privacy_and_webhook_without_polling(monkeypatch):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")
    me = User(id=123456, is_bot=True, first_name="Test", can_read_all_group_messages=False)
    poll = AsyncMock()
    monkeypatch.setattr(bot, "get_me", AsyncMock(return_value=me))
    monkeypatch.setattr(
        bot,
        "get_webhook_info",
        AsyncMock(
            return_value=WebhookInfo(
                url="",
                has_custom_certificate=False,
                pending_update_count=1,
            )
        ),
    )
    monkeypatch.setattr(
        bot,
        "get_chat",
        AsyncMock(
            return_value=ChatFullInfo(
                id=-1001,
                type="supergroup",
                title="Test",
                is_forum=True,
                accent_color_id=1,
                max_reaction_count=1,
                accepted_gift_types={
                    "unlimited_gifts": False,
                    "limited_gifts": False,
                    "unique_gifts": False,
                    "premium_subscription": False,
                    "gifts_from_channels": False,
                },
            )
        ),
    )
    monkeypatch.setattr(
        bot,
        "get_chat_member",
        AsyncMock(
            return_value=ChatMemberMember(
                status="member",
                user=me,
            )
        ),
    )
    monkeypatch.setattr(bot, "get_updates", poll)
    try:
        status = await inspect_bot(bot, -1001)
        assert not status.ready
        assert status.group_is_forum and not status.privacy_disabled
        bot.get_me.return_value = me.model_copy(update={"can_read_all_group_messages": True})
        assert (await inspect_bot(bot, -1001)).ready
        bot.get_webhook_info.return_value = bot.get_webhook_info.return_value.model_copy(
            update={"url": "https://example.invalid/private-hook"}
        )
        assert not (await inspect_bot(bot, -1001)).ready
        poll.assert_not_called()
    finally:
        await bot.session.close()


async def test_discovery_does_not_ack_assign_roles_or_expose_message_text(monkeypatch):
    bot = Bot("123456:FAKE_OFFLINE_TOKEN")

    def update(identifier, text, **changes):
        data = {
            "message_id": identifier,
            "date": 1794873600,
            "chat": {"id": -1001, "type": "supergroup", "title": "Test"},
            "from": {"id": 7, "is_bot": False, "first_name": "Peer"},
            "message_thread_id": 42,
            "text": text,
        }
        data.update(changes)
        return Update.model_validate({"update_id": identifier, "message": data})

    poll = AsyncMock(
        return_value=[
            update(10, "private conversation"),
            update(11, "/peyda_setup", sender_chat={"id": -1001, "type": "supergroup"}),
            update(12, "/peyda_setup"),
            Update(update_id=13),
        ]
    )
    monkeypatch.setattr(bot, "get_updates", poll)
    try:
        bindings = await discover_topic(bot)
        assert len(bindings) == 1
        assert (bindings[0].group_id, bindings[0].topic_id, bindings[0].speaker_id) == (
            -1001,
            42,
            7,
        )
        assert poll.call_args.kwargs["offset"] is None
        assert not hasattr(bindings[0], "text")
    finally:
        await bot.session.close()


async def test_live_health_uses_persistence_without_agent_or_github(tmp_path, monkeypatch):
    from peyda_pm_bot.adapters.telegram import diagnostics

    monkeypatch.chdir(tmp_path)
    for key, value in {
        "PROJECT_ID": "test",
        "GROUP_ID": "-1001",
        "TOPIC_ID": "42",
        "ADMIN_IDS": "[7]",
        "DATABASE_PATH": str(tmp_path / "product.db"),
        "BOT_TOKEN": "123456:FAKE_OFFLINE_TOKEN",
        "GITHUB_REPOSITORY": "example/unused",
    }.items():
        monkeypatch.setenv("PEYDA_" + key, value)
    probe = AsyncMock(
        return_value=diagnostics.BotStatus(
            123456,
            "test_bot",
            False,
            True,
            True,
            "member",
        )
    )
    monkeypatch.setattr(diagnostics, "inspect_bot", probe)
    receive = AsyncMock(return_value=[event()])
    send = AsyncMock(return_value=1000)
    monkeypatch.setattr(AiogramTransport, "receive", receive)
    monkeypatch.setattr(AiogramTransport, "send", send)
    await telegram_command(duration=0.5)
    assert not (tmp_path / "product.db").exists()
    assert send.await_count == 1
    assert send.call_args.args[0].reply_to_id == 100
    path = tmp_path / ".runtime/telegram-smoke.db"
    database = Database(path)
    try:
        await database.initialize(path, "test:telegram-smoke")
        async with database.unit() as unit:
            assert await unit.incoming.offset() == 11
            assert await unit.connection.scalar(select(func.count()).select_from(jobs)) == 0
            assert await unit.connection.scalar(select(outbox.c.status)) == "sent"
    finally:
        await database.close()


async def test_health_mode_refuses_existing_product_database(tmp_path, monkeypatch):
    from peyda_pm_bot.adapters.telegram import diagnostics

    path = tmp_path / "product.db"
    database = Database(path)
    await database.initialize(path, "test")
    await database.close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        Settings,
        "model_config",
        {
            **Settings.model_config,
            "env_file": str(tmp_path / "test.env"),
        },
    )
    (tmp_path / "test.env").write_text(
        "PEYDA_PROJECT_ID=test\nPEYDA_GROUP_ID=-1001\nPEYDA_TOPIC_ID=42\n"
        "PEYDA_ADMIN_IDS=[7]\nPEYDA_DATABASE_PATH=product.db\n"
    )
    monkeypatch.setenv("PEYDA_BOT_TOKEN", "123456:FAKE_OFFLINE_TOKEN")
    monkeypatch.setattr(
        diagnostics,
        "inspect_bot",
        AsyncMock(
            return_value=diagnostics.BotStatus(
                123456,
                "test_bot",
                False,
                True,
                True,
                "member",
            )
        ),
    )
    receive = AsyncMock()
    monkeypatch.setattr(AiogramTransport, "receive", receive)
    with pytest.raises(ValueError, match="another project"):
        await telegram_command(database_path=path, duration=0.1)
    receive.assert_not_called()


async def test_bounded_shutdown_signals_stop_and_remove_timer():
    stop = asyncio.Event()
    with shutdown_signals(stop, 0.01):
        await asyncio.wait_for(stop.wait(), 1)
    assert stop.is_set()


def test_invalid_configuration_output_does_not_print_secrets(monkeypatch, capsys):
    from peyda_pm_bot.bootstrap import main

    secret = "sensitive-test-string"
    monkeypatch.setenv("PEYDA_GROUP_ID", secret)
    monkeypatch.setenv("PEYDA_BOT_TOKEN", "123456:FAKE_OFFLINE_TOKEN")
    # No network request occurs before full validation in health mode (fixed by this check).
    monkeypatch.setattr("sys.argv", ["peyda", "--live-telegram", "--duration", "0.1"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert secret not in output
    assert "group_id" in output


async def test_mcp_permission_requires_registered_tool_session_and_active_turn(tmp_path):
    from acp import schema

    from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
    from peyda_pm_bot.runtime.process import ProcessOwner

    agent = AcpAgent(ProcessOwner(["unused"], tmp_path), tmp_path)
    agent.session_id, agent.in_turn = "active", True
    options = [
        schema.PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
        schema.PermissionOption(option_id="deny", name="Deny", kind="reject_once"),
    ]
    tool = schema.ToolCallUpdate(
        tool_call_id="t",
        name="mcp_peyda-memory_memory_search",
        kind="other",
    )
    assert (await agent.request_permission("active", tool, options)).outcome.option_id == "deny"
    agent.mcp_servers = [
        schema.McpServerStdio(
            name="peyda-memory",
            command="unused",
            args=[],
            env=[],
        )
    ]
    assert (await agent.request_permission("active", tool, options)).outcome.option_id == "allow"
    assert (await agent.request_permission("stale", tool, options)).outcome.option_id == "deny"
    agent.in_turn = False
    assert (await agent.request_permission("active", tool, options)).outcome.option_id == "deny"
    agent.in_turn = True
    unknown = tool.model_copy(update={"name": "mcp_peyda-memory_execute_shell"})
    assert (await agent.request_permission("active", unknown, options)).outcome.option_id == "deny"


async def test_live_polling_modes_share_a_bot_lock(tmp_path, monkeypatch):
    from peyda_pm_bot.runtime.instance import instance_lock

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEYDA_BOT_TOKEN", "123456:FAKE_OFFLINE_TOKEN")
    (tmp_path / ".runtime").mkdir()
    with instance_lock(tmp_path / ".runtime/telegram-123456"):
        with pytest.raises(RuntimeError, match="Another bot"):
            await telegram_command(discover=True)


@pytest.mark.parametrize("allow_keyring", [False, True])
async def test_supervised_environment_keeps_service_tokens_private(
    tmp_path,
    monkeypatch,
    allow_keyring,
):
    import sys

    from peyda_pm_bot.runtime.process import ProcessOwner

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "test-bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("PEYDA_BOT_TOKEN", "private-test-token")
    monkeypatch.setenv("PEYDA_GITHUB_TOKEN", "private-github-test-token")
    owner = ProcessOwner(
        [
            sys.executable,
            "-c",
            "import json,os; print(json.dumps(sorted(os.environ)))",
        ],
        tmp_path,
        desktop_keyring=allow_keyring,
    )
    try:
        process = await owner.start()
        output, _ = await process.communicate()
        keys = set(json.loads(output))
        assert ("DBUS_SESSION_BUS_ADDRESS" in keys) is allow_keyring
        assert ("XDG_RUNTIME_DIR" in keys) is allow_keyring
        assert "PEYDA_BOT_TOKEN" not in keys and "PEYDA_GITHUB_TOKEN" not in keys
    finally:
        await owner.close()
