import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, ReactionTypeEmoji, ReplyParameters

from mimir_pm_bot.adapters.rich_text import rich_text
from mimir_pm_bot.adapters.turn_store import Turns
from mimir_pm_bot.application.turns import Output


def schemas() -> list[dict[str, Any]]:
    result = []
    for kind, extra in {
        "send": ["text"],
        "reply": ["text", "version"],
        "react": ["emoji", "version"],
        "document": ["path"],
    }.items():
        props: dict[str, Any] = {
            "turn": {"type": "integer"},
            "call_key": {"type": "string", "minLength": 1},
        }
        for key in extra:
            props[key] = {"type": "integer" if key == "version" else "string"}
        result.append(
            {
                "name": f"mimir_{kind}",
                "description": {
                    "send": "Send a rich Markdown message in the pm topic.",
                    "reply": "Reply to an exact version with rich Markdown.",
                    "react": "Set a reaction. Empty emoji removes the reaction.",
                    "document": "Send a file. Relative paths use the project root.",
                }[kind],
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        )
    return result


def snapshot(path: Path, spool: Path) -> str:
    if not path.is_file():
        raise ValueError("Expected regular file")
    spool.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=spool, delete=False) as temp:
        tmp = Path(temp.name)
        try:
            with path.open("rb") as src:
                while data := src.read(1024 * 1024):
                    temp.write(data)
                    digest.update(data)
            temp.flush()
            os.fsync(temp.fileno())
            dest = spool / digest.hexdigest()
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    return str(dest) + "\n" + path.name


class Outputs:
    def __init__(self, turns: Turns, bot: Bot, group: int, topic: int, root: Path) -> None:
        self.store, self.bot, self.group, self.topic, self.root = turns, bot, group, topic, root
        self.lock = asyncio.Lock()
        self.turn: int | None = None
        self.rate_until: float = 0

    async def invoke(self, name: str, args: dict[str, Any]) -> str:
        kind = name.removeprefix("mimir_")
        pile = args.get("turn")
        key = args.get("call_key")
        if type(pile) is not int or not isinstance(key, str) or not 0 < len(key) <= 128:
            raise ValueError
        request_hash = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
        prior = await asyncio.to_thread(self.store.receipt, pile, key, request_hash)
        if prior is not None:
            return "Telegram delivery: " + prior
        version = args.get("version")
        if kind in {"reply", "react"} and type(version) is not int:
            raise ValueError
        if kind in {"send", "reply"}:
            text = args.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError
            parts = [text[i : i + 2000] for i in range(0, len(text), 2000)]
        elif kind == "react" and isinstance(args.get("emoji"), str):
            parts = [args["emoji"]]
        elif kind == "document" and isinstance(args.get("path"), str):
            try:
                parts = [
                    await asyncio.to_thread(
                        snapshot,
                        self.root / args["path"],
                        self.store.path.parent / "mimir-documents",
                    )
                ]
            except OSError as exc:
                raise ValueError("File unavailable") from exc
        else:
            raise ValueError
        state = await asyncio.to_thread(
            self.store.stage,
            pile,
            key,
            kind,
            parts,
            version,
            allow_new=pile == self.turn,
            request_hash=request_hash,
        )
        if pile == self.turn and "pending" in state:
            for _ in parts:
                await self.deliver_one()
            state = await asyncio.to_thread(
                self.store.stage,
                pile,
                key,
                kind,
                parts,
                version,
                allow_new=pile == self.turn,
                request_hash=request_hash,
            )
        return "Telegram delivery: " + state

    async def transmit(self, o: Output) -> str:
        if o.kind == "react":
            assert o.target is not None
            await self.bot.set_message_reaction(
                o.chat,
                o.target,
                reaction=[ReactionTypeEmoji(emoji=o.payload)] if o.payload else [],
            )
            return "applied"
        if o.kind == "document":
            path, name = o.payload.split("\n", 1)
            msg = await self.bot.send_document(
                o.chat,
                FSInputFile(path, filename=name),
                message_thread_id=None if o.topic == 1 else o.topic,
            )
            if msg.document:
                return json.dumps({"message": msg.message_id, "file_id": msg.document.file_id})
        else:
            reply = (
                ReplyParameters(message_id=o.target, allow_sending_without_reply=False)
                if o.target is not None
                else None
            )
            try:
                msg = await self.bot.send_rich_message(
                    o.chat,
                    rich_text(o.payload),
                    message_thread_id=None if o.topic == 1 else o.topic,
                    reply_parameters=reply,
                )
            except TelegramBadRequest as exc:
                # Retry literal blocks only after a definitive Markdown parsing rejection.
                if "pars" not in exc.message.lower():
                    raise
                msg = await self.bot.send_rich_message(
                    o.chat,
                    rich_text(o.payload, literal=True),
                    message_thread_id=None if o.topic == 1 else o.topic,
                    reply_parameters=reply,
                )
        return str(msg.message_id)

    async def deliver_one(self) -> None:
        async with self.lock:
            o = await asyncio.to_thread(self.store.next_output)
            if not o:
                return
            state, result, due = "sent", "", 0.0
            try:
                result = await self.transmit(o)
            except TelegramRetryAfter as exc:
                state, result = "pending" if o.attempts < 5 else "failed", "RetryAfter"
                due = time.time() + exc.retry_after
                self.rate_until = due
            except (TelegramBadRequest, TelegramForbiddenError):
                state, result = "failed", "Telegram rejected request"
            except (TelegramAPIError, OSError, TimeoutError):
                state, result = "unknown", "No definitive response"
            await asyncio.to_thread(self.store.settle, o.id, state, result, due)
