"""One supervised agent turn; no network call is made inside a local transaction."""

import asyncio
import logging
import time
from dataclasses import replace

from peyda_pm_bot.application.agent.ports import Agent
from peyda_pm_bot.application.memory.policy import make_policy
from peyda_pm_bot.application.ports import TemporaryFailure, UowFactory
from peyda_pm_bot.domain.agent.models import AgentResult
from peyda_pm_bot.domain.memory.models import MemoryPolicy

logger = logging.getLogger(__name__)

STAGE_LABELS = {
    "thinking": "در حال فکر کردن",
    "writing": "در حال نوشتن",
    "reading": "در حال خواندن",
    "searching": "در حال جست‌وجو",
    "working": "در حال کار",
    "reviewing": "در حال جمع‌بندی",
    "tool_failed": "ابزار به مشکل خورد",
    "github_read": "در حال خواندن گیت‌هاب",
    "github_prepare": "در حال آماده‌کردن گیت‌هاب",
    "memory": "در حال خواندن حافظه",
    "create_file": "در حال ساخت فایل",
}


def presence(stage: str, elapsed: int) -> str:
    """One short rich line. Internal thought and partial answers stay private."""
    label = STAGE_LABELS.get(stage, "در حال بررسی")
    if elapsed <= 0:
        return f"**{label}…**"
    return f"**{label}…** · {elapsed} ثانیه"


class AgentWorker:
    def __init__(
        self,
        uow: UowFactory,
        agent: Agent,
        *,
        timeout: float = 600,
        startup_timeout: float = 30,
        cancel_timeout: float = 5,
        project_id: str = "test",
        memory_policy: MemoryPolicy | None = None,
        start_in_turbo: bool | None = None,
    ) -> None:
        self.project_id = project_id
        self.memory_policy = memory_policy or make_policy()
        self.uow = uow
        self.agent = agent
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self.cancel_timeout = cancel_timeout
        self.lock = asyncio.Lock()
        self.connected = False
        self.turbo_attempted: bool | None = None
        self.start_in_turbo = start_in_turbo

    async def initialize(self) -> None:
        async with self.uow() as unit:
            await unit.queue.recover()
            await unit.queue.turbo_applied(False)
            if self.start_in_turbo is not None:
                await unit.queue.control("/turbo", "on" if self.start_in_turbo else "off")
        self.turbo_attempted = None
        for attempt in range(3):
            try:
                async with self.uow() as unit:
                    state = await unit.queue.state()
                async with asyncio.timeout(self.startup_timeout):
                    session_id = await self.agent.connect(state.session_id)
                async with self.uow() as unit:
                    await unit.queue.connected(session_id)
                self.connected = True
                return
            except TemporaryFailure:
                await self.agent.close()
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)

    async def run_one(self) -> bool:
        async with self.lock:
            return await self._run_one()

    async def _run_one(self) -> bool:
        if not self.connected:
            await self.initialize()
        async with self.uow() as unit:
            state = await unit.queue.state()
        if (
            state.turbo_requested != state.turbo_effective
            and self.turbo_attempted != state.turbo_requested
        ):
            self.turbo_attempted = state.turbo_requested
            applied = await self.agent.set_turbo(state.turbo_requested)
            if applied:
                async with self.uow() as unit:
                    await unit.queue.turbo_applied(state.turbo_requested)
        async with self.uow() as unit:
            request = await unit.queue.claim(time.time())
            if request is not None:
                token = await unit.turns.begin(request, self.memory_policy, self.project_id)
                request = replace(
                    request,
                    memory_token=token,
                    memory_policy=self.memory_policy.text,
                    memory_policy_version=self.memory_policy.version,
                )
        if request is None:
            return False
        result: AgentResult | None = None
        anchor = next(
            (item.message for item in reversed(request.messages) if item.message is not None),
            None,
        )
        started = time.monotonic()

        async def turn() -> None:
            nonlocal result
            async for event in self.agent.run(request):
                if isinstance(event, AgentResult):
                    if result is not None:
                        raise ValueError("Duplicate terminal result")
                    result = event

        task = asyncio.create_task(turn())
        status = "unknown"
        try:
            async with self.uow() as unit:
                await unit.queue.card(
                    presence(self.agent.progress().stage, 0),
                    time.time(),
                    fresh=True,
                    anchor=anchor,
                )
            async with asyncio.timeout(self.timeout):
                while not task.done():
                    async with self.uow() as unit:
                        wanted = await unit.queue.wanted(request)
                        if wanted:
                            await unit.queue.card(
                                presence(
                                    self.agent.progress().stage,
                                    int(time.monotonic() - started),
                                ),
                                time.time(),
                            )
                    if not wanted:
                        await self._stop(task)
                        return True
                    await asyncio.wait({task}, timeout=0.1)
                await task
            if result is None:
                raise ValueError("Missing terminal agent result")
            async with self.uow() as unit:
                if await unit.queue.wanted(request):
                    changed = await unit.memory.commit(request)
                    await unit.actions.publish(request)
                    await unit.queue.complete(request, result)
                    await unit.outgoing.memory_notice(request, changed)
                await unit.queue.finish_card()
            status = "succeeded"
        except asyncio.CancelledError:
            await self._stop(task)
            raise
        except (TimeoutError, ConnectionError):
            await self._stop(task)
        except ValueError as error:
            logger.warning(
                "agent job rejected job_id=%s error_type=%s",
                request.job_id,
                type(error).__name__,
            )
            status = "failed"
            await self._stop(task)
        finally:
            if status != "succeeded":
                async with self.uow() as unit:
                    await unit.queue.fail(request, status)
                    await unit.queue.finish_card()
        return True

    async def _stop(self, task: asyncio.Task[None]) -> None:
        try:
            async with asyncio.timeout(self.cancel_timeout):
                await self.agent.cancel()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Close and reconnect before accepting a retry or another job.
            await self.agent.close()
            self.connected = False
