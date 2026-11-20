"""Composition root for offline demos and explicit live connection checks."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
from peyda_pm_bot.adapters.storage.database import Database
from peyda_pm_bot.adapters.telegram.fake import FakeTransport
from peyda_pm_bot.application.actions.executor import ActionExecutor
from peyda_pm_bot.application.actions.ports import Github
from peyda_pm_bot.application.actions.service import ActionService
from peyda_pm_bot.application.agent.ports import Agent
from peyda_pm_bot.application.agent.worker import AgentWorker
from peyda_pm_bot.application.conversations.service import ConversationPolicy, Conversations
from peyda_pm_bot.application.memory.policy import make_policy
from peyda_pm_bot.application.memory.service import MemoryService
from peyda_pm_bot.application.ports import Transport
from peyda_pm_bot.application.work.service import WorkFollowup, WorkSchedule
from peyda_pm_bot.config import Settings
from peyda_pm_bot.domain.conversations.events import IncomingUpdate, MessageEvent
from peyda_pm_bot.runtime.delivery import Sender
from peyda_pm_bot.runtime.instance import instance_lock


@dataclass(frozen=True)
class Application:
    conversations: Conversations
    sender: Sender
    worker: AgentWorker
    memory: MemoryService
    github: ActionService | None = None
    executor: ActionExecutor | None = None
    followup: WorkFollowup | None = None


@asynccontextmanager
async def application(
    settings: Settings,
    transport: Transport,
    agent: Agent | None = None,
    github: Github | None = None,
) -> AsyncIterator[Application]:
    """Own the process lock, recovery and connections for one application lifetime."""
    path = settings.database_path.resolve()
    database = Database(path)
    concrete_agent = agent or FakeAgent()
    stack = AsyncExitStack()
    provider = github
    try:
        with instance_lock(path) as lock_fd:
            from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent

            if isinstance(concrete_agent, AcpAgent):
                concrete_agent.owner.lock_fd = lock_fd
                concrete_agent.owner.identity_path = path.with_suffix(".agent-process.json")
            await database.initialize(path, settings.project_id)
            memory_policy = make_policy(
                await asyncio.to_thread(settings.memory_policy_path.read_text)
                if settings.memory_policy_path
                else make_policy().text
            )
            if isinstance(concrete_agent, AcpAgent):
                import sys

                from acp import schema

                memory_args = [
                    "-m",
                    "peyda_pm_bot.adapters.memory.server",
                    "--database",
                    str(path),
                    "--project",
                    settings.project_id,
                ]
                if concrete_agent.project_tools:
                    memory_args.extend(("--workspace", str(concrete_agent.root)))
                concrete_agent.mcp_servers = [
                    schema.McpServerStdio(
                        name="peyda-memory",
                        command=sys.executable,
                        args=memory_args,
                        env=[],
                    )
                ]
            action_service = None
            executor = None
            if provider is None and settings.github_repository:
                if (
                    not settings.github_live_validated
                    or not settings.github_token
                    or not settings.github_bot_login
                ):
                    raise ValueError(
                        "Live GitHub configuration requires phase-six validation and credentials"
                    )
                from peyda_pm_bot.adapters.github.http import HttpGithub
                from peyda_pm_bot.adapters.github.rate_limits import PersistentLimits

                provider = HttpGithub(
                    settings.github_repository,
                    settings.github_token.get_secret_value(),
                    settings.github_bot_login,
                    settings.status_labels,
                    rate_limits=PersistentLimits(database.unit),
                )
            if provider is not None:
                if settings.github_repository and provider.repository != settings.github_repository:
                    raise ValueError("GitHub repository does not match configured scope")
                from peyda_pm_bot.adapters.github.hosting import host_server

                async with database.unit() as unit:
                    await unit.actions.bind_repository(provider.repository)
                from peyda_pm_bot.adapters.github.server import make_server

                action_service = ActionService(
                    database.unit,
                    provider,
                    settings.project_id,
                    settings.status_labels,
                    settings.member_logins,
                )
                executor = ActionExecutor(database.unit, provider)
                url = await stack.enter_async_context(host_server(make_server(action_service)))
                if isinstance(concrete_agent, AcpAgent):
                    from acp import schema

                    concrete_agent.mcp_servers.append(
                        schema.HttpMcpServer(name="peyda-github", url=url, headers=[], type="http")
                    )
            policy = ConversationPolicy(
                settings.group_id,
                settings.topic_id,
                settings.admin_ids,
                frozenset(settings.member_logins),
            )
            followup = WorkFollowup(
                database.unit,
                settings.status_labels,
                (settings.group_id, settings.topic_id),
                WorkSchedule(
                    ZoneInfo(settings.report_timezone),
                    settings.report_at,
                    settings.reminder_at,
                    settings.github_sync_seconds,
                    settings.idle_work_hours,
                ),
                provider,
            )
            yield Application(
                Conversations(
                    database.unit,
                    policy,
                    queue_enabled=True,
                    quiet=settings.batch_quiet_seconds,
                    ceiling=settings.batch_ceiling_seconds,
                    followup=followup,
                ),
                Sender(
                    database.unit,
                    transport,
                    max_attempts=settings.max_send_attempts,
                    retry_base=settings.retry_base_seconds,
                ),
                AgentWorker(
                    database.unit,
                    concrete_agent,
                    timeout=settings.agent_timeout_seconds,
                    startup_timeout=settings.agent_startup_seconds,
                    cancel_timeout=settings.agent_cancel_seconds,
                    project_id=settings.project_id,
                    memory_policy=memory_policy,
                    start_in_turbo=(
                        settings.agent_turbo
                        if isinstance(concrete_agent, AcpAgent) and concrete_agent.project_tools
                        else None
                    ),
                ),
                MemoryService(database.unit, settings.project_id),
                action_service,
                executor,
                followup,
            )
    finally:
        try:
            await stack.aclose()
        finally:
            try:
                if provider is not None:
                    await provider.close()
            finally:
                try:
                    await concrete_agent.close()
                finally:
                    try:
                        await transport.close()
                    finally:
                        await database.close()


async def demo(path: Path) -> None:
    settings = Settings(
        _env_file=None,
        github_repository=None,
        project_id="offline-demo",
        group_id=-1000000000001,
        topic_id=42,
        admin_ids=frozenset({123456789}),
        database_path=path,
    )
    transport = FakeTransport()
    async with application(settings, transport) as app:
        offset = await app.conversations.offset()
        transport.updates = [
            IncomingUpdate(
                offset,
                "message",
                MessageEvent(
                    settings.group_id,
                    settings.topic_id,
                    offset + 1,
                    123456789,
                    "Demo Admin",
                    None,
                    None,
                    "/health",
                    datetime.now(UTC),
                    None,
                    False,
                ),
            )
        ]
        await app.conversations.ingest(await transport.receive(offset))
        while await app.conversations.process_one():
            pass
        while await app.sender.send_one():
            pass
        print(f"Offline demo: {len(transport.sent)} response(s); SQLite persistence active.")


def main() -> None:
    import argparse
    import sqlite3

    from pydantic import ValidationError

    from peyda_pm_bot.adapters.antigravity.setup import AgentSetupError

    parser = argparse.ArgumentParser(description="Offline demos and explicit Telegram smoke tests")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--group", type=int, help="Optional group ID for --doctor")
    parser.add_argument("--duration", type=float, help="Stop a timed run after this many seconds")
    parser.add_argument("--project-id", help="Expected project identity for backup or restore")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--phase-two", action="store_true")
    modes.add_argument("--phase-three", action="store_true")
    modes.add_argument("--phase-four", action="store_true")
    modes.add_argument("--phase-five", action="store_true", help="Run the offline follow-up demo")
    modes.add_argument("--backup-to", type=Path, help="Copy --database to a new snapshot file")
    modes.add_argument("--restore-from", type=Path, help="Restore a snapshot to a new --database")
    modes.add_argument("--doctor", action="store_true", help="Read-only Telegram connection check")
    modes.add_argument("--discover-telegram", action="store_true", help="Find /peyda_setup topic")
    modes.add_argument("--live-telegram", action="store_true", help="Real Telegram /health only")
    modes.add_argument("--live-agent", action="store_true", help="Real Telegram, agent and memory")
    modes.add_argument("--serve", action="store_true", help="Run the integrated Telegram service")
    args = parser.parse_args()
    if args.duration is not None and not 0 < args.duration <= 3600:
        parser.error("--duration must be greater than zero and at most 3600")
    if args.group is not None and args.group >= 0:
        parser.error("--group must be a negative group ID")
    live = (
        args.doctor or args.discover_telegram or args.live_telegram or args.live_agent or args.serve
    )
    snapshot_mode = args.backup_to is not None or args.restore_from is not None
    if args.project_id is not None and not snapshot_mode:
        parser.error("--project-id applies only to backup and restore")
    if not live and args.database is None:
        parser.error("Offline demos and snapshot commands require --database")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # HTTP debug/INFO records may include credential-bearing Telegram URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if snapshot_mode:
        from peyda_pm_bot.adapters.storage.backup import copy_snapshot

        source = args.database if args.backup_to else args.restore_from
        destination = args.backup_to or args.database
        try:
            snapshot = copy_snapshot(source, destination, args.project_id)
        except (OSError, ValueError, sqlite3.Error) as error:
            parser.exit(2, f"Snapshot failed: {error}\n")
        print(f"SQLite snapshot verified (schema {snapshot.revision}): {destination}")
        return
    if live:
        try:
            asyncio.run(
                telegram_command(
                    doctor=args.doctor,
                    discover=args.discover_telegram,
                    group_id=args.group,
                    database_path=args.database,
                    duration=args.duration,
                    agent_enabled=args.live_agent,
                    serve=args.serve,
                )
            )
        except ValidationError as error:
            fields = sorted(
                {".".join(str(part) for part in item["loc"]) for item in error.errors()}
            )
            parser.exit(2, "Invalid or missing configuration fields: " + ", ".join(fields) + "\n")
        except AgentSetupError as error:
            parser.exit(2, str(error) + "\n")
        except Exception as error:
            # Exceptions may carry request URLs, credentials or private payloads.
            parser.exit(2, f"Live check stopped ({type(error).__name__}); see LIVE_TESTING.md.\n")
        return
    asyncio.run(
        demo_five(args.database, args.duration)
        if args.phase_five
        else demo_four(args.database)
        if args.phase_four
        else demo_three(args.database)
        if args.phase_three
        else demo_two(args.database)
        if args.phase_two
        else demo(args.database)
    )


async def telegram_command(
    *,
    doctor: bool = False,
    discover: bool = False,
    group_id: int | None = None,
    database_path: Path | None = None,
    duration: float | None = None,
    agent_enabled: bool = False,
    serve: bool = False,
) -> None:
    """Run the integrated service or an explicitly limited connectivity test."""
    import json
    from dataclasses import asdict

    from aiogram import Bot

    from peyda_pm_bot.adapters.telegram.aiogram_transport import AiogramTransport
    from peyda_pm_bot.adapters.telegram.diagnostics import discover_topic, inspect_bot
    from peyda_pm_bot.config import TelegramSettings
    from peyda_pm_bot.runtime.runner import run, shutdown_signals

    credentials = TelegramSettings()
    if credentials.bot_token is None:
        raise ValueError("PEYDA_BOT_TOKEN is required")
    transport = AiogramTransport(Bot(credentials.bot_token.get_secret_value()))
    locks = ExitStack()
    try:
        if not doctor:
            lock_root = Path(".runtime")
            await asyncio.to_thread(lock_root.mkdir, exist_ok=True)
            locks.enter_context(instance_lock(lock_root / f"telegram-{transport.bot.id}"))
        if doctor or discover:
            status = await inspect_bot(transport.bot, group_id)
            print(json.dumps(asdict(status), ensure_ascii=False))
            if discover:
                if status.webhook_active:
                    raise ValueError("Polling conflicts with an existing webhook")
                bindings = await discover_topic(transport.bot)
                print(json.dumps([asdict(item) for item in bindings]))
                if not bindings:
                    print("No setup message found in the first 100 pending updates; no ACK sent.")
            return
        settings = Settings()
        agent_enabled |= serve
        agent = None
        if agent_enabled:
            from peyda_pm_bot.adapters.antigravity.setup import live_agent

            agent = await live_agent(
                settings.antigravity_cli_path,
                settings.agent_workspace_path,
                settings.agent_conversation_id,
                desktop_keyring=settings.agent_desktop_keyring,
                http_mcp=serve and settings.github_repository is not None,
                project_tools=serve,
            )
        path = (
            database_path
            or (settings.database_path if agent_enabled else Path(".runtime/telegram-smoke.db"))
        ).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        status = await inspect_bot(transport.bot, settings.group_id)
        if not status.ready:
            print(json.dumps(asdict(status)))
            raise ValueError("Check webhook, forum membership and Privacy Mode")
        if agent is not None:
            settings = settings.model_copy(update={"database_path": path})
            if not serve:
                settings = settings.model_copy(
                    update={
                        "github_repository": None,
                        "github_token": None,
                        "github_bot_login": None,
                    }
                )
            async with application(settings, transport, agent) as app:
                await app.worker.initialize()
                stop = asyncio.Event()
                status_text = (
                    "Integrated service started."
                    if serve
                    else ("Live agent test started; memory enabled, GitHub disabled.")
                )
                print(status_text, flush=True)
                with shutdown_signals(stop, duration):
                    await run(
                        app.conversations,
                        app.sender,
                        transport,
                        stop,
                        app.worker,
                        action_executor=app.executor if serve else None,
                        followup=app.followup if serve else None,
                    )
            return
        database = Database(path)
        try:
            with instance_lock(path):
                await database.initialize(path, settings.project_id + ":telegram-smoke")
                conversations = Conversations(
                    database.unit,
                    ConversationPolicy(
                        settings.group_id,
                        settings.topic_id,
                        settings.admin_ids,
                    ),
                )
                sender = Sender(
                    database.unit,
                    transport,
                    max_attempts=settings.max_send_attempts,
                    retry_base=settings.retry_base_seconds,
                )
                stop = asyncio.Event()
                print(
                    "Live Telegram health test started; agent and GitHub are disabled.", flush=True
                )
                with shutdown_signals(stop, duration):
                    await run(conversations, sender, transport, stop)
        finally:
            await database.close()
    finally:
        try:
            await transport.close()
        finally:
            locks.close()


async def demo_two(path: Path) -> None:
    from peyda_pm_bot.domain.agent.models import AgentResult

    settings = Settings(
        _env_file=None,
        github_repository=None,
        project_id="offline-phase-two",
        group_id=-1000000000001,
        topic_id=42,
        admin_ids=frozenset({123456789}),
        database_path=path,
    )
    transport = FakeTransport()
    agent = FakeAgent()
    agent.results = [AgentResult("reply", "پاسخ نهایی آزمایشی؛ دو پیام دریافت شد.")]
    async with application(settings, transport, agent) as app:
        offset = await app.conversations.offset()
        for index in range(2):
            event = IncomingUpdate(
                offset + index,
                "message",
                MessageEvent(
                    settings.group_id,
                    settings.topic_id,
                    offset + index + 1,
                    123456789 + index,
                    "Demo speaker",
                    None,
                    None,
                    "پیام آزمایشی",
                    datetime.now(UTC),
                    None,
                    False,
                ),
            )
            await app.conversations.ingest([event])
            await app.conversations.process_one()
        await asyncio.sleep(4.1)
        await app.worker.run_one()
        while await app.sender.send_one():
            pass
        print(
            f"Phase two offline: {len(agent.requests)} job; {agent.peak} peak agent turn; "
            "shared session; final reply and status card delivered."
        )


async def demo_three(path: Path) -> None:
    from peyda_pm_bot.adapters.memory.demo_agent import MemoryDemoAgent

    settings = Settings(
        _env_file=None,
        github_repository=None,
        project_id="offline-phase-three",
        group_id=-1000000000001,
        topic_id=42,
        admin_ids=frozenset({123456789}),
        database_path=path,
        batch_quiet_seconds=0.01,
        batch_ceiling_seconds=0.02,
    )
    for phase, texts in enumerate(
        (
            ("ثبت تصمیم ورود", "بحث پرداخت"),
            ("بازیابی تصمیم ورود", "اصلاح تصمیم ورود", "بازیابی تصمیم ورود"),
        )
    ):
        agent = MemoryDemoAgent()
        transport = FakeTransport()
        async with application(settings, transport, agent) as app:
            agent.memory = app.memory
            for text in texts:
                offset = await app.conversations.offset()
                message = MessageEvent(
                    settings.group_id,
                    settings.topic_id,
                    offset + 1,
                    123456789,
                    "Demo peer",
                    None,
                    None,
                    text,
                    datetime.now(UTC),
                    None,
                    False,
                )
                await app.conversations.ingest([IncomingUpdate(offset, "message", message)])
                await app.conversations.process_one()
                await asyncio.sleep(0.02)
                await app.worker.run_one()
                while await app.sender.send_one():
                    pass
            print(
                f"Offline memory phase {phase + 1}: {len(transport.sent)} outputs; "
                "case/decision, topic switch, restart and sourced recall exercised."
            )


async def demo_four(path: Path) -> None:
    from peyda_pm_bot.adapters.github.demo_agent import GithubDemoAgent
    from peyda_pm_bot.adapters.github.fake import FakeGithub

    settings = Settings(
        _env_file=None,
        github_repository=None,
        project_id="offline-phase-four",
        group_id=-1000000000001,
        topic_id=42,
        admin_ids=frozenset({123456789}),
        database_path=path,
        member_logins={123456789: "alice", 123456790: "bob", 123456791: "carol"},
        batch_quiet_seconds=0.01,
        batch_ceiling_seconds=0.02,
    )
    agent = GithubDemoAgent()
    github = FakeGithub()
    transport = FakeTransport()
    async with application(settings, transport, agent, github) as app:
        agent.actions = app.github
        agent.memory = app.memory

        async def input_message(text: str) -> None:
            offset = await app.conversations.offset()
            message = MessageEvent(
                settings.group_id,
                settings.topic_id,
                offset + 1,
                123456789,
                "Demo peer",
                None,
                None,
                text,
                datetime.now(UTC),
                None,
                False,
            )
            await app.conversations.ingest([IncomingUpdate(offset, "message", message)])
            await app.conversations.process_one()

        await input_message("قابلیت ورود را تسک کنیم")
        await asyncio.sleep(0.02)
        await app.worker.run_one()
        assert app.executor is not None and agent.last_action is not None
        github.lose_next_reply = True
        await app.executor.run_one()
        await input_message("/retry_action " + agent.last_action.id)
        await app.executor.run_one()
        await input_message("شروع کار ورود؛ تداخل و پیش‌نیاز را بررسی کن")
        await asyncio.sleep(0.02)
        await app.worker.run_one()
        await app.executor.run_one()
        while await app.sender.send_one():
            pass
        issue = github.issue_data[1]
        print(
            f"Offline GitHub demo: {len(github.issue_data)} issue, {github.mutations} mutations; "
            f"lost-create reply reconciled without duplication; labels={issue.labels}."
        )


async def demo_five(path: Path, duration: float | None = None) -> None:
    from datetime import time, timedelta

    from peyda_pm_bot.adapters.github.fake import FakeGithub
    from peyda_pm_bot.domain.actions.models import RemoteIssue
    from peyda_pm_bot.domain.work.models import BranchProtection, PullSummary
    from peyda_pm_bot.runtime.runner import run, shutdown_signals

    github = FakeGithub()
    settings = Settings(
        _env_file=None,
        project_id="offline-phase-five",
        group_id=-1000000000001,
        topic_id=42,
        admin_ids=frozenset({123456789}),
        database_path=path,
        github_repository=github.repository,
        github_token=None,
        bot_token=None,
        memory_policy_path=None,
        report_at=time.min,
        reminder_at=time.min,
    )
    now = datetime.now(UTC)
    previous = (now - timedelta(days=4)).isoformat()
    url = f"https://github.com/{github.repository}"
    github.issue_data[1] = RemoteIssue(
        1, "دریافت ورودی", "", "open", ("pm:blocked",), ("alice",), previous, f"{url}/issues/1"
    )
    github.pull_data[2] = PullSummary(
        2, "آزمون ورودی", f"{url}/pull/2", "open", False, now.isoformat()
    )
    github.protection = BranchProtection(True, True, True)
    transport = FakeTransport()
    async with application(settings, transport, github=github) as app:
        assert app.followup is not None
        await app.followup.run_once()
        offset = await app.conversations.offset()
        transport.updates = [
            IncomingUpdate(
                offset,
                "message",
                MessageEvent(
                    settings.group_id,
                    settings.topic_id,
                    offset + 1,
                    123456789,
                    "Demo peer",
                    None,
                    None,
                    "/report",
                    now,
                    None,
                    False,
                ),
            )
        ]
        stop = asyncio.Event()
        with shutdown_signals(stop, duration or 1):
            await run(app.conversations, app.sender, transport, stop, followup=app.followup)
    print(
        f"Offline phase five: {len(transport.sent)} report/reminder deliveries, "
        f"{github.mutations} GitHub writes."
    )
