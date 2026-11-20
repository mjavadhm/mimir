"""Offline protocol peer: small independent JSON-RPC wire fixture."""

import json
import sys


def send(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    result = {}
    if method == "initialize":
        result = {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True},
            "authMethods": [],
        }
        if "--antigravity-1.5.10" in sys.argv:
            result["agentInfo"] = {"name": "Antigravity ACP Server", "version": "1.5.10"}
    elif method == "session/new":
        assert "--load-only" not in sys.argv
        result = {"sessionId": "offline-acp-session"}
    elif method == "session/load" and "--load-only" in sys.argv:
        expected = sys.argv[sys.argv.index("--load-only") + 1]
        params = request["params"]
        assert params["cwd"] == sys.argv[sys.argv.index("--load-only") + 2]
        assert params["sessionId"] == expected
        if "--missing-conversation" in sys.argv:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32602, "message": "Conversation not found"},
                }
            )
            continue
    elif method == "session/prompt":
        texts = (
            ("**پاسخ**", "\n- جزئیات")
            if "--markdown-reply" in sys.argv
            else ('{"disposition":"reply","text":"offline response"}',)
        )
        for text in texts:
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "offline-acp-session",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    },
                }
            )
        result = {"stopReason": "end_turn"}
    if "id" in request:
        send({"jsonrpc": "2.0", "id": request["id"], "result": result})
