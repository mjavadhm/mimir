from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SetupChange:
    actor: int
    step: Literal["begin", "group", "activate", "abort"]
    group: int = 0
    topic: int = 0
