"""Lifecycle-owned, loopback-only MCP HTTP host; no public network listener."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.fastmcp import FastMCP


@asynccontextmanager
async def host_server(server: FastMCP) -> AsyncIterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    runner = uvicorn.Server(
        uvicorn.Config(
            server.streamable_http_app(), log_level="warning", access_log=False, lifespan="on"
        )
    )
    task = asyncio.create_task(runner.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not runner.started:
                if task.done():
                    await task
                    raise RuntimeError("MCP host did not start")
                await asyncio.wait({task}, timeout=0.01)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        runner.should_exit = True
        try:
            await asyncio.wait_for(task, 10)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        listener.close()
