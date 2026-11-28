import asyncio
import hashlib
import json
import logging
import os
import shlex
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot

from mimir_pm_bot.adapters.acp import Agent
from mimir_pm_bot.adapters.github import GitHub
from mimir_pm_bot.adapters.incidents import IncidentChannel, record_exception, report
from mimir_pm_bot.adapters.memory import (
    TOOLS,
    Memory,
    committed_decision,
    memory_schemas,
    work_source,
)
from mimir_pm_bot.adapters.outputs import Outputs, schemas
from mimir_pm_bot.adapters.piles import BatchWindow
from mimir_pm_bot.adapters.private_panel import PrivatePanel
from mimir_pm_bot.adapters.quota import read_quota
from mimir_pm_bot.adapters.review_store import ReviewStore
from mimir_pm_bot.adapters.review_worker import ReviewWorker
from mimir_pm_bot.adapters.storage import Store
from mimir_pm_bot.adapters.telegram import Telegram
from mimir_pm_bot.adapters.tool_server import tool_server
from mimir_pm_bot.adapters.turn_cards import TurnCards
from mimir_pm_bot.adapters.turn_store import Turns
from mimir_pm_bot.adapters.work import Work
from mimir_pm_bot.application.reviews import Review, ReviewAction
from mimir_pm_bot.application.turns import Control
from mimir_pm_bot.application.work import TOOLS as WORK_TOOLS
from mimir_pm_bot.profiles import read_prompt, servers
from mimir_pm_bot.runtime.collector import collect, exclusive
from mimir_pm_bot.runtime.service import Worker


def configuration() -> dict[str, str]:
    values: dict[str, str] = {}
    file = Path(".env")
    for line in file.read_text().splitlines() if file.is_file() else ():
        key, _, raw = line.partition("=")
        if key in {
            "PEYDA_GITHUB_TOKEN",
            "PEYDA_GITHUB_BOT_LOGIN",
            "PEYDA_BOT_TOKEN",
            "PEYDA_GROUP_ID",
            "PEYDA_TOPIC_ID",
            "PEYDA_ADMIN_IDS",
            "PEYDA_AGENT_WORKSPACE_PATH",
            "PEYDA_AGENT_CONVERSATION_ID",
            "PEYDA_AGENT_DESKTOP_KEYRING",
        } or key.startswith("MIMIR_"):
            words = shlex.split(raw, comments=True)
            if words:
                values[key] = " ".join(words) if key.endswith("_ADMIN_IDS") else words[0]
    values.update(os.environ)

    return values


def settings() -> tuple[str, int, int, Path, int, BatchWindow]:
    values = configuration()

    def required(key: str) -> str:
        for prefix in ("MIMIR_", "PEYDA_"):
            if value := values.get(prefix + key):
                return value
        raise ValueError("Missing MIMIR_" + key)

    path = Path(values.get("MIMIR_DATABASE_PATH", ".runtime/mimir-inbox.db")).resolve()
    return (
        required("BOT_TOKEN"),
        int(values.get("MIMIR_GROUP_ID") or values.get("PEYDA_GROUP_ID") or 0),
        int(values.get("MIMIR_TOPIC_ID") or values.get("PEYDA_TOPIC_ID") or 0),
        path,
        int(values.get("MIMIR_POLL_TIMEOUT", "20")),
        BatchWindow(
            timedelta(seconds=float(values.get("MIMIR_BATCH_QUIET_SECONDS", "4"))),
            timedelta(seconds=float(values.get("MIMIR_BATCH_CEILING_SECONDS", "60"))),
        ),
    )


async def run() -> None:
    token, group, topic, path, timeout, window = settings()
    if timeout <= 0:
        raise ValueError("MIMIR_POLL_TIMEOUT must be positive")
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(path):
        async with Bot(token).context() as bot:
            store = Store(path, window)
            await asyncio.to_thread(store.initialize, bot.id, group, topic)
            group, topic = await asyncio.to_thread(store.destination)
            if not group:
                raise ValueError("Configure the destination using --serve and /bind")
            await collect(Telegram(bot, group, topic, timeout), store)


async def serve() -> None:
    values = configuration()

    def setting(k: str, default: str = "") -> str:
        return values.get("MIMIR_" + k, values.get("PEYDA_" + k, default))

    token, group, topic, path, timeout, window = settings()
    if not setting("AGENT_WORKSPACE_PATH") or not setting("AGENT_CONVERSATION_ID"):
        raise ValueError("Configure the project root and conversation id")
    root = await asyncio.to_thread(Path(setting("AGENT_WORKSPACE_PATH")).resolve, strict=True)
    session = setting("AGENT_CONVERSATION_ID")
    ids = json.loads(setting("ADMIN_IDS", "[]"))
    if not isinstance(ids, list) or not ids or any(type(n) is not int or n <= 0 for n in ids):
        raise ValueError("MIMIR_ADMIN_IDS must be a JSON array of positive Telegram user IDs")
    admins = frozenset(ids)
    cap = int(setting("PROMPT_BYTES", "32768"))
    interval = float(setting("WORKER_INTERVAL", "0.3"))
    if not (0 < cap <= 32768 and interval > 0 and timeout > 0):
        raise ValueError("Invalid polling or measured prompt budget")
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".cache"))) / "mimir-locks"
    guard.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive(path), exclusive(guard / hashlib.sha256(os.fsencode(root)).hexdigest()) as fd:
        async with Bot(token).context() as bot:
            store = Store(path, window)
            await asyncio.to_thread(store.initialize, bot.id, group, topic)
            turns = Turns(path)
            await asyncio.to_thread(turns.recover)
            agent = Agent(
                root,
                session,
                shutil.which("agy-cli") or "agy-cli",
                setting("AGENT_DESKTOP_KEYRING").lower() == "true",
            )
            agent.guard_fd = fd
            outputs = Outputs(turns, bot, group, topic, root)
            cards = TurnCards(turns, bot, group, topic)

            async def activity(label: str) -> None:
                if outputs.turn is not None:
                    await asyncio.to_thread(cards.activity, outputs.turn, label)

            agent.activity = activity
            quota_lock = asyncio.Lock()

            async def quota() -> str:
                async with quota_lock:
                    return await asyncio.to_thread(read_quota, agent.binary, root, agent.keyring)

            worker = Worker(turns, agent, outputs, cap, interval, quota=quota)

            async def capture(component: str, exc: Exception) -> None:
                await asyncio.to_thread(record_exception, turns, component, exc, outputs.turn)

            telegram = Telegram(bot, group, topic, timeout, admins)
            telegram.on_error = capture

            panel = PrivatePanel(
                bot, admins, max(1.0, float(setting("SCREEN_INTERVAL", "4"))), capture
            )

            async def private(control: Control, text: str) -> None:
                await panel.send(control, text, worker.panel, lambda: outputs.turn)

            memory = Memory(turns.transaction)

            reviewer_mcp = servers("reviewer")
            reviewer_prompt = read_prompt("reviewer")
            reviews = None
            review_worker = None
            review_tools: dict[str, tuple[type[Review] | type[ReviewAction], str]] = {
                "review_pr": (Review, "Delegate a PR review requested in this Telegram turn."),
                "publish_review": (ReviewAction, "Publish an explicitly requested saved review."),
                "cancel_review": (ReviewAction, "Cancel the requested review."),
            }
            work = None
            work_schemas = []
            if repo := values.get("MIMIR_GITHUB_REPOSITORY"):
                github = GitHub(repo, setting("GITHUB_TOKEN"), setting("GITHUB_BOT_LOGIN"))
                await github.verify()
                work = Work(turns.transaction, github, committed_decision, work_source)
                await asyncio.to_thread(work.bind)
                work_schemas = [
                    {"name": name, "description": desc, "inputSchema": model.model_json_schema()}
                    for name, (model, desc) in WORK_TOOLS.items()
                ]

                reviews = ReviewStore(turns.transaction, work_source, github.repo, reviewer_prompt)

                def make_reviewer(directory: Path, session_id: str) -> Agent:
                    reviewer = Agent(directory, session_id, agent.binary, agent.keyring)
                    reviewer.guard_fd = fd
                    return reviewer

                review_worker = ReviewWorker(
                    reviews,
                    github,
                    path.parent / (path.stem + "-reviews"),
                    make_reviewer,
                    reviewer_mcp,
                    work.review_context,
                )
                await asyncio.to_thread(reviews.recover)
                work_schemas.extend(
                    {"name": name, "description": desc, "inputSchema": model.model_json_schema()}
                    for name, (model, desc) in review_tools.items()
                )

            async def invoke(name: str, args: dict[str, Any]) -> str:
                if reviews and name in review_tools:
                    if outputs.turn is None or args.get("turn") != outputs.turn:
                        raise ValueError("Expired review turn")
                    if name == "review_pr":
                        return await asyncio.to_thread(
                            reviews.stage, Review.model_validate(args), "publish"
                        )
                    return await asyncio.to_thread(
                        reviews.stage,
                        ReviewAction.model_validate(args),
                        "cancel" if name == "cancel_review" else "publish",
                    )
                if work and name in WORK_TOOLS:
                    if outputs.turn is None or args.get("turn") != outputs.turn:
                        raise ValueError("Expired work turn")
                    return await work.invoke(name, args)
                if name in TOOLS:
                    if outputs.turn is None or args.get("turn") != outputs.turn:
                        raise ValueError("Expired memory turn")
                    return await memory.invoke(name, args)
                return await outputs.invoke(name, args)

            async with tool_server(
                schemas() + memory_schemas() + work_schemas, invoke, capture
            ) as url:
                try:
                    await agent.start(url)
                    print("Mimir ready: bound session and Telegram collector", flush=True)
                    async with asyncio.TaskGroup() as tasks:
                        tasks.create_task(telegram.collect_managed(store))
                        tasks.create_task(worker.run())
                        tasks.create_task(worker.controls(admins, private))
                        tasks.create_task(worker.delivery())
                        tasks.create_task(cards.run(interval))
                        if channel := values.get("MIMIR_LOG_CHANNEL_ID"):
                            tasks.create_task(IncidentChannel(turns, bot, int(channel)).run())
                        if review_worker:
                            tasks.create_task(review_worker.jobs(interval))
                            tasks.create_task(review_worker.publications(interval))
                        if work:
                            tasks.create_task(work.delivery(interval))
                finally:
                    await panel.close()
                    await agent.close()


async def report_crash(exc: Exception) -> None:
    while isinstance(exc, ExceptionGroup):
        exc = exc.exceptions[0]
    try:
        values = configuration()
        channel = values.get("MIMIR_LOG_CHANNEL_ID")
        if channel:
            token = values.get("MIMIR_BOT_TOKEN") or values["PEYDA_BOT_TOKEN"]
            async with asyncio.timeout(8), Bot(token).context() as bot:
                await bot.send_rich_message(
                    int(channel),
                    report("Service stopped", type(exc).__name__, "Check the service journal"),
                )
    except Exception as reporting_error:
        logging.getLogger(__name__).error(
            "Crash notification failed (%s)", type(reporting_error).__name__
        )
