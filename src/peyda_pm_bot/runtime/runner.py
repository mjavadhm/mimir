"""Owned concurrent workers; ingestion never waits for an agent or sender."""

import asyncio
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager

from peyda_pm_bot.application.actions.executor import ActionExecutor
from peyda_pm_bot.application.agent.worker import AgentWorker
from peyda_pm_bot.application.conversations.service import Conversations
from peyda_pm_bot.application.ports import TemporaryFailure, Transport
from peyda_pm_bot.application.work.service import WorkFollowup
from peyda_pm_bot.runtime.delivery import Sender

logger = logging.getLogger(__name__)


@contextmanager
def shutdown_signals(stop: asyncio.Event, duration: float | None = None) -> Iterator[None]:
    """Own signal handlers and an optional bounded live-smoke-test lifetime."""
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for item in signals:
        loop.add_signal_handler(item, stop.set)
    timer = loop.call_later(duration, stop.set) if duration is not None else None
    try:
        yield
    finally:
        if timer is not None:
            timer.cancel()
        for item in signals:
            loop.remove_signal_handler(item)


async def pause(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run(
    conversations: Conversations,
    sender: Sender,
    transport: Transport,
    stop: asyncio.Event,
    agent_worker: AgentWorker | None = None,
    action_executor: ActionExecutor | None = None,
    followup: WorkFollowup | None = None,
) -> None:
    async def ingest() -> None:
        failures = 0
        while not stop.is_set():
            try:
                updates = await transport.receive(await conversations.offset())
                await conversations.ingest(updates)
                failures = 0
                if not updates:
                    await pause(stop, 0.1)
            except TemporaryFailure as error:
                failures += 1
                if failures >= 6:
                    raise RuntimeError(
                        "Receive failed six consecutive times; restart after recovery"
                    ) from None
                logger.warning("receive unavailable; retry=%s", failures)
                await pause(stop, max(error.retry_after, 2 ** (failures - 1)))

    async def process() -> None:
        while not stop.is_set():
            if not await conversations.process_one():
                await pause(stop, 0.1)

    async def deliver() -> None:
        while not stop.is_set():
            if not await sender.send_one():
                await pause(stop, 0.1)

    async def agent_loop() -> None:
        if agent_worker is None:
            return
        while not stop.is_set():
            if not await agent_worker.run_one():
                await pause(stop, 0.1)

    async def action_loop() -> None:
        if action_executor is None:
            return
        while not stop.is_set():
            if not await action_executor.run_one():
                await pause(stop, 0.1)

    async def followup_loop() -> None:
        if followup is None:
            return
        while not stop.is_set():
            await followup.run_once()
            await pause(stop, 30)

    try:
        async with asyncio.TaskGroup() as group:
            workers = [
                group.create_task(worker())
                for worker in (ingest, process, deliver, agent_loop, action_loop, followup_loop)
            ]
            await stop.wait()
            # Give local transactions a chance to finish before cancelling blocked I/O.
            _, pending = await asyncio.wait(workers, timeout=2)
            for worker in pending:
                worker.cancel()
    finally:
        try:
            if agent_worker:
                await agent_worker.agent.close()
        finally:
            await transport.close()
