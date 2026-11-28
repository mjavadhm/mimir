"""Loopback MCP Streamable HTTP endpoint. A fresh secret route is bound per service."""

import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import web


@asynccontextmanager
async def tool_server(
    schemas: list[dict[str, Any]],
    invoke: Callable[[str, dict[str, Any]], Awaitable[str]],
    on_error: Callable[[str, Exception], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    path = "/" + secrets.token_urlsafe(32)

    async def handle(req: web.Request) -> web.Response:
        msg = await req.json()
        if not isinstance(msg, dict):
            raise web.HTTPBadRequest()
        method = msg.get("method")
        if "id" not in msg:
            return web.Response(status=202)
        base = {"jsonrpc": "2.0", "id": msg["id"]}
        params = msg.get("params", {})
        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mimir", "version": "0.1"},
            }
        elif method == "tools/list":
            result = {"tools": schemas}
        elif method == "ping":
            result = {}
        elif method == "tools/call":
            try:
                value = await invoke(params["name"], params.get("arguments", {}))
            except (ValueError, KeyError, TypeError):
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": "Invalid tool request"}],
                }
            except Exception as exc:
                if on_error:
                    await on_error("Tool execution", exc)
                raise
            else:
                result = {"content": [{"type": "text", "text": value}]}
        else:
            return web.json_response({**base, "error": {"code": -32601, "message": "Unsupported"}})
        return web.Response(
            text=json.dumps({**base, "result": result}), content_type="application/json"
        )

    app = web.Application()
    app.router.add_post(path, handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        yield f"http://127.0.0.1:{runner.addresses[0][1]}{path}"
    finally:
        await runner.cleanup()
