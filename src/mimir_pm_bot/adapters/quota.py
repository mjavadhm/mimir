"""Read the signed-in CLI's local /usage screen without prompting its model."""

import codecs
import os
import pty
import re
import select
import signal
import subprocess
import termios
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import pyte


def parse_usage(screen: str) -> list[str]:
    rows: list[str] = []
    group = window = ""
    for raw in screen.splitlines():
        line = raw.strip()
        if re.fullmatch(r"[A-Z][A-Z0-9 /&()+.\-]{1,70}", line):
            group, window = line, ""
        elif line.endswith(" Limit Remaining"):
            window = line.removesuffix(" Limit Remaining")
        elif group and window and (found := re.search(r"\]\s*(\d+(?:\.\d+)?)%", line)):
            if 0 <= float(found[1]) <= 100:
                rows.append(f"{group} · {window}: {found[1]}٪ باقی‌مانده")
                window = ""
        elif rows and line.startswith(("Refreshes in ", "Quota available")):
            rows[-1] += f" — {line}"
    return rows


def read_quota(binary: str, root: Path, keyring: bool) -> str:
    names = {"HOME", "PATH", "LANG", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
    if keyring:
        names.update({"DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"})
    env = {k: v for k, v in os.environ.items() if k in names}
    env["TERM"] = "xterm-256color"
    master, slave = pty.openpty()
    proc = None
    try:
        termios.tcsetwinsize(slave, (90, 140))
        proc = subprocess.Popen(
            [binary],
            cwd=root,
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        slave = -1
        display = pyte.Screen(140, 90)
        stream = pyte.Stream(display)
        decode = codecs.getincrementaldecoder("utf-8")(errors="replace")
        deadline = time.monotonic() + 35
        typed_at = entered_at = 0.0
        retry = False
        last = ""
        changed = time.monotonic()
        while time.monotonic() < deadline and proc.poll() is None:
            if select.select([master], [], [], 0.15)[0]:
                data = os.read(master, 65536)
                if not data:
                    break
                stream.feed(decode.decode(data))
                if b"\x1b[6n" in data:
                    os.write(
                        master, f"\x1b[{display.cursor.y + 1};{display.cursor.x + 1}R".encode()
                    )
                if b"\x1b[c" in data:
                    os.write(master, b"\x1b[?1;2c")
            screen = "\n".join(display.display)
            now = time.monotonic()
            if last != screen:
                last, changed = screen, now
            rows = parse_usage(screen)
            if rows and now - changed >= 0.7:
                stamp = datetime.now(UTC).strftime("%H:%M UTC")
                return f"سهمیهٔ Antigravity · {stamp}\n" + "\n".join(rows)
            if not typed_at and re.search(r"^\s*>\s*", screen, re.M):
                os.write(master, b"/usage")
                typed_at = now
            elif typed_at and not entered_at and now - typed_at >= 0.6:
                os.write(master, b"\r")
                entered_at = now
            elif (
                entered_at
                and not retry
                and now - entered_at > 1
                and re.search(r"^\s*>\s*/usage\s*$", screen, re.M)
            ):
                os.write(master, b"\r")
                retry = True
        raise RuntimeError("CLI usage screen unavailable")
    finally:
        if proc is not None:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        os.close(master)
        if slave != -1:
            os.close(slave)
