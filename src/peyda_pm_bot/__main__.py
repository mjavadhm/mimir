"""Command line. `--live-telegram` is the only path that reads `.env`."""

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from peyda_pm_bot.bootstrap import App, build
from peyda_pm_bot.config import Settings, settings_from_environ
from peyda_pm_bot.runtime.cycle import iter_cycles
from peyda_pm_bot.runtime.lock import InstanceLocked, ProcessLock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Peyda phase-one Telegram ingest")
    parser.add_argument("--live-telegram", action="store_true")
    parser.add_argument("--database", type=Path, default=Path(".runtime/phase-one.db"))
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.live_telegram:
        parser.error("--live-telegram is required in this phase")
    env_file = args.env_file
    if env_file is None and Path(".env").is_file():
        env_file = Path(".env")
    if env_file is not None:
        _load_env_file(env_file)
    try:
        settings = settings_from_environ(args.database)
    except ValueError as exc:
        print(exc)
        return 1
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("peyda.ingest").setLevel(logging.INFO)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        return asyncio.run(_live(settings))
    except InstanceLocked:
        print("lock=busy")
        return 3
    except Exception as exc:
        print(f"live_failed={type(exc).__name__}")
        return 1


def _load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def _live(settings: Settings) -> int:
    lock = ProcessLock(settings.database_path)
    lock.acquire()
    app: App | None = None
    accepted = 0
    ignored = 0
    checkpoint = 0
    plain = False
    replay_ok = False
    try:
        app = build(settings)
        opener = app.opener()
        received_at = datetime.now(UTC)
        async for _outcomes in iter_cycles(
            app.telegram,
            opener,
            app.telegram,
            settings.allowlist(),
            received_at,
            limit=5,
        ):
            pass
        accepted, ignored, checkpoint, plain = await app.snapshot()
        replay_ok = await app.replay_duplicates(received_at)
    finally:
        if app is not None:
            await app.close()
        lock.release()
    agy = settings.agy_cli_path
    print(f"agy_cli={agy if agy is not None else 'missing'}")
    print("agy_acp=not_started")
    print("gui_launcher_untouched=/usr/local/bin/agy")
    print(f"accepted={accepted}")
    print(f"ignored={ignored}")
    print(f"checkpoint={checkpoint}")
    print(f"replay_duplicates={'yes' if replay_ok else 'no'}")
    print(f"plain_message_seen={'yes' if plain else 'no'}")
    if accepted < 1 or not replay_ok:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
