"""Validate the complete intent list before any output can be persisted."""

import json
from dataclasses import asdict

from peyda_pm_bot.domain.agent.models import (
    AgentRequest,
    AgentResult,
    SendMessage,
    SetReaction,
    TelegramAction,
)


def validate_actions(request: AgentRequest, result: AgentResult) -> tuple[TelegramAction, ...]:
    evidence = [item.message for item in request.messages if item.message is not None]
    if not evidence or result.disposition not in {"reply", "silent"}:
        raise ValueError("Invalid agent result")
    if result.actions and (result.text or result.reply_to_id is not None):
        raise ValueError("Cannot mix legacy response and actions")
    actions = result.actions
    if result.text:
        # Compatibility for phase-two callers: the legacy default replied to the last message.
        target = result.reply_to_id if result.reply_to_id is not None else evidence[-1].message_id
        actions = (SendMessage(result.text, target),)
    if result.disposition == "silent":
        if actions or result.reply_to_id is not None:
            raise ValueError("Silent result cannot contain actions")
        return ()
    if not actions or len(actions) > 20:
        raise ValueError("Expected between one and twenty actions")
    identities = {message.message_id for message in evidence}
    if len({(message.chat_id, message.topic_id) for message in evidence}) != 1:
        raise ValueError("Request must belong to one chat/topic")
    total = 0
    reactions: set[int] = set()
    for action in actions:
        if isinstance(action, SendMessage):
            if not action.text.strip():
                raise ValueError("Empty message")
            total += len(action.text)
            if action.reply_to_id is not None and action.reply_to_id not in identities:
                raise ValueError("Reply target outside request")
        elif isinstance(action, SetReaction):
            if action.message_id not in identities or action.message_id in reactions:
                raise ValueError("Reaction target outside request or duplicated")
            if action.emoji is not None and (not action.emoji.strip() or len(action.emoji) > 32):
                raise ValueError("Invalid reaction emoji")
            reactions.add(action.message_id)
        else:
            raise ValueError("Unsupported Telegram action")
    if total > 128000:
        raise ValueError("Response exceeds limit")
    return actions


def encode_result(result: AgentResult, actions: tuple[TelegramAction, ...]) -> str:
    return json.dumps(
        {
            "disposition": result.disposition,
            "actions": [
                {"kind": "message" if isinstance(item, SendMessage) else "reaction", **asdict(item)}
                for item in actions
            ],
        },
        ensure_ascii=False,
    )
