"""A Linux process lock prevents competing startup recovery and senders."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def instance_lock(database_path: Path) -> Iterator[int]:
    with database_path.with_suffix(database_path.suffix + ".lockfile").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another bot process owns this database") from None
        try:
            yield handle.fileno()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
