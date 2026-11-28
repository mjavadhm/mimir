from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    pile: int
    prompt: str


@dataclass(frozen=True)
class Output:
    id: int
    kind: str
    payload: str
    target: int | None
    attempts: int
    chat: int
    topic: int


@dataclass(frozen=True)
class Control:
    actor: int
    command: str
    pile: int | None
