"""One database file accepts one ingest process."""

from pathlib import Path

import pytest

from peyda_pm_bot.runtime.lock import InstanceLocked, ProcessLock


def test_second_process_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "phase-one.db"
    first = ProcessLock(database)
    second = ProcessLock(database)
    first.acquire()
    try:
        with pytest.raises(InstanceLocked, match="phase-one.db.lock"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
    second.release()
