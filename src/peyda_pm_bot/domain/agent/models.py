"""Agent contracts contain no framework or transport details."""

from dataclasses import dataclass, field

from peyda_pm_bot.domain.conversations.events import IncomingUpdate


@dataclass(frozen=True)
class AgentRequest:
    job_id: int
    revision: int
    generation: int
    messages: tuple[IncomingUpdate, ...]
    memory_token: str | None = field(default=None, repr=False)
    memory_policy: str = ""
    memory_policy_version: str = ""


@dataclass(frozen=True)
class SendMessage:
    text: str
    reply_to_id: int | None = None


@dataclass(frozen=True)
class SetReaction:
    message_id: int
    emoji: str | None


TelegramAction = SendMessage | SetReaction


@dataclass(frozen=True)
class AgentResult:
    disposition: str
    text: str = ""
    reply_to_id: int | None = None
    actions: tuple[TelegramAction, ...] = ()


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    text: str = ""


@dataclass(frozen=True)
class AgentProgress:
    stage: str = "thinking"
    model: str | None = None


@dataclass(frozen=True)
class SessionState:
    session_id: str | None
    generation: int
    blocked: bool
    turbo_requested: bool
    turbo_effective: bool
