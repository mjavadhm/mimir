"""Small I/O boundaries shared by ingestion, processing and delivery."""

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from peyda_pm_bot.application.actions.ports import ActionStore
from peyda_pm_bot.application.actions.rate_ports import RateLimits
from peyda_pm_bot.application.agent.ports import QueueStore
from peyda_pm_bot.application.agent.turn_ports import TurnStore
from peyda_pm_bot.application.memory.ports import MemoryStore
from peyda_pm_bot.application.work.ports import WorkStore
from peyda_pm_bot.domain.actions.models import ActionRun, RemoteComment, RemoteIssue
from peyda_pm_bot.domain.agent.models import AgentRequest
from peyda_pm_bot.domain.conversations.events import Delivery, IncomingUpdate
from peyda_pm_bot.domain.memory.models import Evidence, MemoryRecord


class Transport(Protocol):
    async def receive(self, offset: int) -> list[IncomingUpdate]: ...
    async def send(self, delivery: Delivery) -> int: ...
    async def close(self) -> None: ...


class TemporaryFailure(Exception):
    """The operation is known not to have been accepted; retry is safe."""

    def __init__(self, retry_after: float = 0) -> None:
        self.retry_after = retry_after
        super().__init__("Temporary transport failure")


class UnknownOutcome(Exception):
    """Delivery may have succeeded; blind retries are unsafe."""


class PermanentFailure(Exception):
    """The request cannot succeed without a configuration or input change."""


class IncomingStore(Protocol):
    async def source_ids(self, chat_id: int, message_id: int) -> tuple[int, ...]: ...
    async def search_evidence(self, query: str) -> list[Evidence]: ...
    async def offset(self) -> int: ...
    async def ingest(self, update: IncomingUpdate, accepted: bool) -> None: ...
    async def advance(self, offset: int) -> None: ...
    async def next_input(self) -> IncomingUpdate | None: ...
    async def finish_input(self, update: IncomingUpdate, response: str | None) -> None: ...
    async def recover(self, project_id: str) -> None: ...


class OutgoingStore(Protocol):
    async def scheduled_notice(self, key: str, group: int, topic: int, text: str) -> None: ...
    async def action_notice(
        self, run: ActionRun, status: str, remote: RemoteIssue | RemoteComment | None, detail: str
    ) -> None: ...
    async def memory_notice(self, request: AgentRequest, records: list[MemoryRecord]) -> None: ...
    async def claim_delivery(self, now: datetime) -> Delivery | None: ...
    async def finish_delivery(
        self, delivery_id: int, status: str, external_id: int | None, due: datetime
    ) -> None: ...


class Store(IncomingStore, OutgoingStore, Protocol):
    """Compatibility view; new use cases depend on their narrower capability."""


class UnitOfWork(Protocol):
    @property
    def github_limits(self) -> RateLimits: ...

    @property
    def turns(self) -> TurnStore: ...
    @property
    def actions(self) -> ActionStore: ...
    @property
    def work(self) -> WorkStore: ...

    @property
    def memory(self) -> MemoryStore: ...

    @property
    def incoming(self) -> IncomingStore: ...

    @property
    def outgoing(self) -> OutgoingStore: ...

    @property
    def store(self) -> Store: ...

    @property
    def queue(self) -> QueueStore: ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


UowFactory = Callable[[], UnitOfWork]
