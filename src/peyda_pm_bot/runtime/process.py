"""Linux process supervisor: child holds the instance lock until it has exited."""

import asyncio
import json
import os
import signal
from pathlib import Path


class ProcessOwner:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        lock_fd: int | None = None,
        *,
        desktop_keyring: bool = False,
    ) -> None:
        self.identity_path: Path | None = None
        self.command = command
        self.cwd = cwd
        self.lock_fd = lock_fd
        self.desktop_keyring = desktop_keyring
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> asyncio.subprocess.Process:
        # A dedicated launcher installs parent-death handling before exec.
        import sys

        launcher = Path(__file__).with_name("process_launcher.py")
        await asyncio.to_thread(self._check_identity)
        environment_keys = {"PATH", "HOME", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        if self.desktop_keyring:
            # Explicit opt-in: Linux Secret Service uses the desktop bus for OAuth.
            environment_keys.update({"DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"})
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(launcher),
            str(os.getpid()),
            *self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(() if self.lock_fd is None else (self.lock_fd,)),
            env={key: value for key, value in os.environ.items() if key in environment_keys},
        )
        await asyncio.to_thread(self._save_identity)
        return self.process

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        self.process = None
        if self.identity_path:
            self.identity_path.unlink(missing_ok=True)

    def _check_identity(self) -> None:
        if self.process is not None and self.process.returncode is None:
            raise RuntimeError("Agent process already running")
        if self.identity_path and self.identity_path.exists():
            identity = json.loads(self.identity_path.read_text())
            previous = Path(f"/proc/{identity['pid']}/stat")
            if (
                previous.exists()
                and previous.read_text().split(") ", 1)[1].split()[19] == identity["start"]
            ):
                raise RuntimeError("Previous agent guardian still exists; refusing replacement")

    def _save_identity(self) -> None:
        if self.identity_path and self.process:
            process_stat = Path(f"/proc/{self.process.pid}/stat").read_text()
            self.identity_path.write_text(
                json.dumps(
                    {"pid": self.process.pid, "start": process_stat.split(") ", 1)[1].split()[19]}
                )
            )
