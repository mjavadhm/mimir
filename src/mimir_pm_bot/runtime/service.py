import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from mimir_pm_bot.application.turns import Control, Turn


class Session(Protocol):
    model: str
    mode: str
    text: str
    step: str
    models: set[str]
    active: bool

    @property
    def connected(self) -> bool: ...

    rate_event: str

    async def prompt(self, text: str) -> str: ...
    async def cancel(self) -> None: ...
    async def select_model(self, model: str) -> None: ...


class Queue(Protocol):
    def claim(self, cap: int) -> Turn | None: ...
    def finish(self, pile: int, state: str, detail: str) -> str: ...
    def control(self) -> Control | None: ...
    def status(self) -> str: ...
    def request_model(self, model: str | None) -> None: ...
    def pending_model(self) -> str | None: ...


class Delivery(Protocol):
    turn: int | None
    rate_until: float

    async def deliver_one(self) -> None: ...


class Worker:
    def __init__(
        self,
        q: Queue,
        agent: Session,
        out: Delivery,
        cap: int,
        interval: float,
        quota: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.q, self.agent, self.out, self.cap, self.interval = q, agent, out, cap, interval
        self.quota = quota
        self.model_lock = asyncio.Lock()
        self.last_stop = "idle"

    async def run(self) -> None:
        while True:
            async with self.model_lock:
                requested = await asyncio.to_thread(self.q.pending_model)
                if requested:
                    if self.agent.model != requested:
                        await self.agent.select_model(requested)
                    await asyncio.to_thread(self.q.request_model, None)
                turn = await asyncio.to_thread(self.q.claim, self.cap)
                if turn:
                    self.out.turn = turn.pile
            if not turn:
                await asyncio.sleep(self.interval)
                continue
            try:
                stop = await self.agent.prompt(turn.prompt)
            except (RuntimeError, TimeoutError, OSError):
                self.last_stop = "unknown"
                await asyncio.to_thread(self.q.finish, turn.pile, "unknown", "ACP outcome unknown")
                continue
            finally:
                self.out.turn = None
            self.last_stop = await asyncio.to_thread(self.q.finish, turn.pile, "done", stop)

    async def delivery(self) -> None:
        while True:
            await self.out.deliver_one()
            await asyncio.sleep(self.interval)

    async def panel(self, control: Control) -> str:
        cmd, _, arg = control.command.partition(" ")
        if cmd == "/limits":
            try:
                report = await self.quota() if self.quota else "گزارش سهمیه در دسترس نیست."
            except (OSError, RuntimeError, TimeoutError):
                report = "خواندن گزارش /usage از Antigravity موفق نشد."
            return (
                f"{report}\nAntigravity: {self.agent.rate_event}\n"
                f"Telegram RetryAfter (Unix): {self.out.rate_until or '—'}"
            )
        if cmd == "/screen":
            state = await asyncio.to_thread(self.q.status)
            return (
                f"ACP: {'connected' if self.agent.connected else 'disconnected'}"
                f" | turn: {self.out.turn} | last: {self.last_stop}\n"
                f"model: {self.agent.model} | mode: {self.agent.mode}\n"
                f"Turbo: CLI auto-accept requested\nqueue: {state}\n"
                f"tool: {self.agent.step[:240]}\n{self.agent.text[-2400:]}"
            )
        if cmd == "/cancel":
            if self.out.turn is None or self.out.turn != control.pile or not self.agent.active:
                return f"No matching active turn. Last result: {self.last_stop}"
            await self.agent.cancel()
            return f"Cancel sent for turn {control.pile}. /screen shows its result."
        if cmd == "/model":
            if not arg:
                return self.agent.model + "\n" + "\n".join(sorted(self.agent.models))
            if arg not in self.agent.models:
                return "Unknown model"
            async with self.model_lock:
                if self.out.turn is not None:
                    await asyncio.to_thread(self.q.request_model, arg)
                    return "Model change queued for the turn boundary: " + arg
                await self.agent.select_model(arg)
                await asyncio.to_thread(self.q.request_model, None)
                return "ACP selected: " + self.agent.model
        return "Unknown command"

    async def controls(
        self, admins: frozenset[int], send: Callable[[Control, str], Awaitable[None]]
    ) -> None:
        async def respond(control: Control) -> None:
            try:
                reply = await self.panel(control)
            except (ValueError, RuntimeError, TimeoutError):
                reply = "ACP control was not confirmed"
            await send(control, reply)

        async with asyncio.TaskGroup() as tasks:
            pending: dict[int, asyncio.Task[None]] = {}
            while True:
                control = await asyncio.to_thread(self.q.control)
                if control and control.actor in admins:
                    if control.command == "/limits":
                        previous = pending.get(control.actor)
                        if previous is None or previous.done():
                            pending[control.actor] = tasks.create_task(respond(control))
                    else:
                        await respond(control)
                else:
                    await asyncio.sleep(self.interval)
