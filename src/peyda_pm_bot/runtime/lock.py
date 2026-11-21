"""Exclusive process lock for one database."""

import fcntl
from pathlib import Path
from types import TracebackType
from typing import TextIO


class InstanceLocked(RuntimeError):
    """Another process already holds the ingest lock."""


class ProcessLock:
    def __init__(self, database_path: Path) -> None:
        self.path = database_path.with_name(database_path.name + ".lock")
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise InstanceLocked(self.path.name) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
