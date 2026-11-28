"""Explicit live ACP probe. Print capability metadata, never conversation history."""

import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any


def config(key: str) -> str:
    for prefix in ("MIMIR_AGENT_", "PEYDA_AGENT_"):
        name = prefix + key
        if os.environ.get(name):
            return os.environ[name]
        for line in Path(".env").read_text().splitlines():
            k, _, value = line.partition("=")
            if k == name:
                return shlex.split(value)[0]
    raise ValueError(f"Missing {key}")


def probe() -> None:
    root = Path(config("WORKSPACE_PATH")).resolve(strict=True)
    session = config("CONVERSATION_ID")
    binary = shutil.which("agy-cli")
    if not binary:
        raise FileNotFoundError("agy-cli")
    names = {"HOME", "PATH", "LANG", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
    if config("DESKTOP_KEYRING").lower() == "true":
        names.update({"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"})
    env = {k: v for k, v in os.environ.items() if k in names}
    with subprocess.Popen(
        [binary, "acp"],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    ) as proc:
        assert proc.stdin and proc.stdout
        writer, reader = proc.stdin, proc.stdout
        chunks: list[str] = []

        def send(obj: dict[str, Any]) -> None:
            writer.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
            writer.flush()

        def request(n: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
            send({"id": n, "method": method, "params": params})
            for line in reader:
                msg = json.loads(line)
                if "method" in msg:
                    if "id" in msg:
                        send({"id": msg["id"], "error": {"code": -32601, "message": "unsupported"}})
                    elif n == 3:
                        update = msg.get("params", {}).get("update", {})
                        if update.get("sessionUpdate") == "agent_message_chunk":
                            chunks.append(update.get("content", {}).get("text", ""))
                    continue
                if msg.get("id") == n:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: ACP error {msg['error'].get('code')}")
                    return dict(msg["result"])
            raise RuntimeError(f"{method}: CLI exited ({proc.poll()})")

        try:
            caps = request(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            print("initialize:", caps.get("agentInfo"), caps.get("agentCapabilities"), flush=True)
            if not caps.get("agentCapabilities", {}).get("loadSession"):
                raise RuntimeError("loadSession unavailable")
            load = request(
                2, "session/load", {"cwd": str(root), "sessionId": session, "mcpServers": []}
            )
            print("load: OK, fields:", sorted(load), flush=True)
            for group, key in (("models", "currentModelId"), ("modes", "currentModeId")):
                print(key, (load.get(group) or {}).get(key), flush=True)
            print(
                "config options:",
                [o.get("id") for o in load.get("configOptions") or []],
                flush=True,
            )
            token = secrets.token_hex(8)
            result = request(
                3,
                "session/prompt",
                {
                    "sessionId": session,
                    "prompt": [
                        {
                            "type": "text",
                            "text": f"Mimir connectivity check. Reply only {token}. Use no tools.",
                        }
                    ],
                },
            )
            if token not in "".join(chunks):
                raise RuntimeError("Prompt marker missing")
            print("prompt: marker verified", result.get("stopReason"), sorted(result), flush=True)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)


if __name__ == "__main__":

    def deadline(signum: int, frame: object) -> None:
        raise TimeoutError("ACP probe timed out")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(90)
    probe()
