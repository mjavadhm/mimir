"""Filesystem policy is enforced independently of prompt text or tool titles."""

from pathlib import Path


class ReadPolicy:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def allowed(self, operation: str, target: str) -> bool:
        if operation != "read":
            return False
        requested = self.root / target
        resolved = requested.resolve()
        if (
            not requested.is_relative_to(self.root)
            or not resolved.is_relative_to(self.root)
            or not resolved.is_file()
        ):
            return False
        parts = (*requested.relative_to(self.root).parts, *resolved.relative_to(self.root).parts)
        return not any(
            part.startswith(".env")
            or part in {".git", ".ssh", ".aws", ".codex", ".agents", ".runtime", ".tools"}
            or part.endswith((".pem", ".key", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"))
            for part in parts
        )
