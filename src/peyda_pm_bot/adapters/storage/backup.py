"""Create a checked, standalone SQLite snapshot and publish it without overwrite."""

import os
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from alembic.script import ScriptDirectory

from peyda_pm_bot.adapters.storage.database import migration_config


@dataclass(frozen=True)
class Snapshot:
    project_id: str
    revision: str


def _validate(connection: sqlite3.Connection, path: Path) -> Snapshot:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite foreign key check failed")
    project = connection.execute("SELECT project_id FROM checkpoint WHERE id=1").fetchone()
    revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if (
        project is None
        or not isinstance(project[0], str)
        or not project[0].strip()
        or len(revisions) != 1
    ):
        raise ValueError("Invalid project identity or schema version")
    revision = revisions[0][0]
    versions = {
        item.revision
        for item in ScriptDirectory.from_config(migration_config(path)).walk_revisions()
    }
    if revision not in versions:
        raise ValueError("Unsupported SQLite schema version")
    return Snapshot(project[0], revision)


def copy_snapshot(source: Path, destination: Path, expected_project: str | None = None) -> Snapshot:
    """Copy a live or backed-up database. The destination must be a new file."""
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Snapshot source must be an existing file")
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Snapshot destination already exists")
    if any(os.path.lexists(f"{destination}{suffix}") for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("Snapshot destination has existing SQLite sidecars")
    descriptor, name = tempfile.mkstemp(prefix=".peyda-snapshot-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        deadline = time.monotonic() + 30

        def check_deadline(status: int, remaining: int, total: int) -> None:
            if time.monotonic() > deadline:
                raise TimeoutError("SQLite snapshot timed out")

        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin,
            closing(sqlite3.connect(temporary)) as copy,
        ):
            origin.backup(copy, pages=64, progress=check_deadline, sleep=0.1)
            mode = copy.execute("PRAGMA journal_mode=DELETE").fetchone()
            if mode != ("delete",):
                raise ValueError("Could not make the snapshot standalone")
            snapshot = _validate(copy, temporary)
            if expected_project is not None and snapshot.project_id != expected_project:
                raise ValueError("Snapshot belongs to another project")
        with temporary.open("rb") as verified:
            os.fsync(verified.fileno())
        # Publish atomically while refusing a destination created by a concurrent process.
        os.link(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return snapshot
    finally:
        temporary.unlink(missing_ok=True)
