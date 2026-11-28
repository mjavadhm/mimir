import asyncio
import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mimir_pm_bot.application.conversations import Inbox, Source


@contextmanager
def exclusive(path: Path) -> Iterator[int]:
    with path.with_suffix(path.suffix + ".lockfile").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another collector owns this database") from exc
        yield lock.fileno()


async def collect(source: Source, inbox: Inbox) -> None:
    while True:
        offset = await asyncio.to_thread(inbox.checkpoint)
        for event in await source.fetch(offset):
            await asyncio.to_thread(inbox.record, event)
