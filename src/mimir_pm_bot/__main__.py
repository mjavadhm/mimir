import argparse
import asyncio
import signal
import sys

from aiogram.exceptions import TelegramAPIError
from aiohttp import ClientError

from mimir_pm_bot.bootstrap import report_crash, run, serve


def main() -> None:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--collect", action="store_true")
    modes.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    async def entry() -> None:
        task = asyncio.current_task()
        assert task is not None
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await (serve() if args.serve else run())
        except Exception as exc:
            await report_crash(exc)
            raise

    try:
        asyncio.run(entry())
    except (TelegramAPIError, ClientError, TimeoutError) as exc:
        sys.exit(f"Telegram collection stopped ({type(exc).__name__})")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
