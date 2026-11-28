"""One ACP child and one reader for concurrent control and session notifications."""

import asyncio
import json
import os
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from mimir_pm_bot.profiles import servers


def activity_label(update: dict[str, Any]) -> str:
    title = str(update.get("title", "")).lower()
    known = {
        "memory_search": "Searching project memory",
        "memory_read": "Reading a decision",
        "memory_stage": "Saving a decision",
        "work_status": "Checking work status",
        "prepare_issue": "Preparing an issue",
        "submit_issue": "Submitting an issue",
        "mimir_reply": "Replying in the topic",
        "mimir_send": "Sending a message",
    }
    for tool, label in known.items():
        if tool in title:
            return label
    return {
        "read": "Reading a file",
        "edit": "Editing a file",
        "search": "Searching",
        "execute": "Running a command",
        "fetch": "Fetching information",
        "delete": "Deleting a file",
        "move": "Moving a file",
        "think": "Thinking",
    }.get(str(update.get("kind")), "Using a tool")


class ACPError(RuntimeError):
    pass


class Agent:
    def __init__(self, root: Path, session: str, binary: str, keyring: bool) -> None:
        self.root, self.session = root, session
        self.binary, self.keyring = binary, keyring
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.review_output = False
        self.overflow = False
        self.guard_fd: int | None = None
        self.requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.seq = 0
        self.model = self.mode = "unknown"
        self.models: set[str] = set()
        self.text = self.step = ""
        self.activity: Callable[[str], Awaitable[None]] | None = None
        self.seen_tools: set[str] = set()
        self.active = False
        self.rate_event = "unknown"
        self.usage: dict[str, int] = {}

    @property
    def connected(self) -> bool:
        return bool(
            self.reader and not self.reader.done() and self.proc and self.proc.returncode is None
        )

    async def start(self, url: str = "", *, mcp: list[dict[str, Any]] | None = None) -> None:
        names = {"HOME", "PATH", "LANG", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
        if self.keyring:
            names.update({"DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"})
        self.proc = await asyncio.create_subprocess_exec(
            self.binary,
            "acp",
            "--dangerously-skip-permissions",
            cwd=self.root,
            env={k: v for k, v in os.environ.items() if k in names},
            stdin=-1,
            stdout=-1,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(() if self.guard_fd is None else (self.guard_fd,)),
            limit=16 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read())
        caps = await self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        if not caps.get("agentCapabilities", {}).get("loadSession"):
            raise ACPError("loadSession unavailable")
        params: dict[str, Any] = {
            "cwd": str(self.root),
            "mcpServers": servers("mimir", url) if mcp is None else mcp,
        }
        if self.session:
            params["sessionId"] = self.session
            loaded = await self.call("session/load", params)
            if loaded.get("sessionId", self.session) != self.session:
                raise ACPError("Unexpected session binding")
        else:
            loaded = await self.call("session/new", params)
            if not isinstance(loaded.get("sessionId"), str) or not loaded["sessionId"]:
                raise ACPError("Missing review session ID")
            self.session = loaded["sessionId"]
        models = loaded.get("models") or {}
        self.model = models.get("currentModelId", "unknown")
        self.models = {v["modelId"] for v in models.get("availableModels", [])}
        self.mode = (loaded.get("modes") or {}).get("currentModeId", "unknown")

    async def _send(self, packet: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise ACPError("CLI unavailable")
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", **packet}) + "\n").encode())
        await self.proc.stdin.drain()

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while line := await self.proc.stdout.readline():
                msg = json.loads(line)
                if "method" in msg:
                    if "id" in msg:
                        await self._send(
                            {
                                "id": msg["id"],
                                "error": {"code": -32601, "message": "Unsupported client method"},
                            }
                        )
                    elif self.active and msg.get("params", {}).get("sessionId") == self.session:
                        update = msg["params"].get("update", {})
                        kind = update.get("sessionUpdate")
                        if kind == "agent_message_chunk":
                            text = self.text + update.get("content", {}).get("text", "")
                            if self.review_output:
                                self.overflow |= len(text) > 32000
                                self.text = text[:32000]
                            else:
                                self.text = text[-3200:]
                        elif kind in {"tool_call", "tool_call_update"}:
                            self.step = update.get("title") or self.step
                            ident = update.get("toolCallId")
                            if (
                                self.activity
                                and isinstance(ident, str)
                                and ident not in self.seen_tools
                            ):
                                self.seen_tools.add(ident)
                                await self.activity(activity_label(update))
                    continue
                future = self.requests.get(msg.get("id"))
                if future and not future.done():
                    if "error" in msg:
                        detail = str(msg["error"]).lower()
                        if "rate limit" in detail or "resource_exhausted" in detail:
                            self.rate_event = "provider reported rate limit"
                        future.set_exception(ACPError(f"ACP error {msg['error'].get('code')}"))
                    else:
                        future.set_result(msg["result"])
        finally:
            for future in self.requests.values():
                if not future.done():
                    future.set_exception(ACPError("CLI disconnected"))

    async def call(
        self, method: str, params: dict[str, Any], deadline_seconds: int = 90
    ) -> dict[str, Any]:
        self.seq += 1
        ident = self.seq
        future = asyncio.get_running_loop().create_future()
        self.requests[ident] = future
        try:
            await self._send({"id": ident, "method": method, "params": params})
            return await asyncio.wait_for(future, deadline_seconds)
        finally:
            self.requests.pop(ident, None)

    async def prompt(self, text: str) -> str:
        if self.active:
            raise ACPError("Turn already active")
        self.overflow = False
        self.seen_tools.clear()
        self.active = True
        self.text = self.step = ""
        try:
            result = await self.call(
                "session/prompt",
                {"sessionId": self.session, "prompt": [{"type": "text", "text": text}]},
                600,
            )
            self.usage = {k: v for k, v in (result.get("usage") or {}).items() if type(v) is int}
            return str(result["stopReason"])
        finally:
            self.active = False

    async def cancel(self) -> None:
        if self.active:
            await self._send({"method": "session/cancel", "params": {"sessionId": self.session}})

    async def select_model(self, model: str) -> None:
        if self.active or model not in self.models:
            raise ValueError("Model unavailable or turn active")
        await self.call("session/set_model", {"sessionId": self.session, "modelId": model})
        self.model = model

    async def close(self) -> None:
        if self.proc:
            with suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                await self.proc.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
