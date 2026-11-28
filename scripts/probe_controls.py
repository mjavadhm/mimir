"""Verify private control capabilities on the same ACP conversation, restore the initial model."""

import asyncio
import shutil
from pathlib import Path

from probe_antigravity import config

from mimir_pm_bot.adapters.acp import Agent
from mimir_pm_bot.adapters.tool_server import tool_server


async def main() -> None:
    agent = Agent(
        Path(config("WORKSPACE_PATH")),
        config("CONVERSATION_ID"),
        shutil.which("agy-cli") or "agy-cli",
        config("DESKTOP_KEYRING") == "true",
    )

    async def unused(name, args):
        raise ValueError

    async with tool_server([], unused) as url:
        try:
            await agent.start(url)
            initial = agent.model
            try:
                other = next(x for x in sorted(agent.models) if x != initial)
                await agent.select_model(other)
                print("set_model acknowledged", other, flush=True)
            finally:
                await agent.select_model(initial)
            task = asyncio.create_task(
                agent.prompt("Mimir cancel check. Count upwards in plain text. Use no tools.")
            )
            await asyncio.sleep(1)
            await agent.cancel()
            print("cancel stop reason", await asyncio.wait_for(task, 50), flush=True)
        finally:
            await agent.close()


asyncio.run(main())
