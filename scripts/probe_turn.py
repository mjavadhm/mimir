"""Measure one marked synthetic prompt including a real MCP tool call."""

import argparse
import asyncio
import secrets
import shutil
from pathlib import Path

from probe_antigravity import config

from mimir_pm_bot.adapters.acp import Agent
from mimir_pm_bot.adapters.tool_server import tool_server


async def run(size: int) -> None:
    agent = Agent(
        Path(config("WORKSPACE_PATH")),
        config("CONVERSATION_ID"),
        shutil.which("agy-cli") or "agy-cli",
        config("DESKTOP_KEYRING") == "true",
    )
    proof = secrets.token_hex(8)
    start, end = secrets.token_hex(6), secrets.token_hex(6)
    calls = 0

    async def ping(name, args):
        nonlocal calls
        if name != "mimir_probe" or args.get("first") != start or args.get("last") != end:
            raise ValueError
        calls += 1
        return proof

    schema = {
        "name": "mimir_probe",
        "description": "Echo proof for two observed markers.",
        "inputSchema": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("first", "last")},
            "required": ["first", "last"],
        },
    }
    head = f"First marker: {start}. Call mimir_probe with both markers. Reply with its proof.\n"
    tail = f"\nLast marker: {end}"
    prompt = head + secrets.token_hex(size)[: size - len(head) - len(tail)] + tail
    async with tool_server([schema], ping) as url:
        try:
            await agent.start(url)
            print(
                "loaded model",
                agent.model,
                "mode",
                agent.mode,
                "choices",
                len(agent.models),
                flush=True,
            )
            stop = await agent.prompt(prompt)
            if calls < 1 or proof not in agent.text or stop != "end_turn":
                raise RuntimeError(
                    f"Marker or tool verification failed; tool calls={calls}, stop={stop}"
                )
            print(
                "verified",
                len(prompt.encode()),
                "bytes; tool calls",
                calls,
                "usage fields",
                sorted(agent.usage),
            )
        finally:
            await agent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bytes", type=int, default=8192)
    asyncio.run(run(parser.parse_args().bytes))
