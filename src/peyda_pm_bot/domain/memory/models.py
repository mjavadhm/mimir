"""Versioned, source-backed memory, independent of frameworks and model providers."""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Literal

MemoryKind = Literal["case", "note", "link", "summary", "architecture_draft"]
NoteKind = Literal["proposal", "decision", "commitment", "question", "interpretation"]


@dataclass(frozen=True)
class MemoryRecord:
    kind: MemoryKind
    title: str
    text: str = ""
    id: str = ""
    revision: int = 0
    category: NoteKind | None = None
    status: Literal["active", "inactive"] = "active"
    case_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    source_update_ids: tuple[int, ...] = ()
    attributed_user_ids: tuple[int, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class MemoryPolicy:
    text: str
    version: str


@dataclass(frozen=True)
class MemoryTurn:
    id: int
    job_id: int
    revision: int
    generation: int


@dataclass(frozen=True)
class MemoryView:
    record: MemoryRecord
    needs_review: bool = False
    staged: bool = False
    updated_at: str = ""
    superseded: bool = False


@dataclass(frozen=True)
class Evidence:
    update_id: int
    message_id: int
    chat_id: int
    speaker_id: int | None
    text: str | None
    reply_to_id: int | None
    sent_at: str
    edited_at: str | None
    superseded: bool
    speaker_name: str | None = None
    sender_chat_id: int | None = None
    topic_id: int | None = None


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).translate(str.maketrans("يىك\u200c", "ییک "))
    return re.sub(r"\s+", " ", value).strip().casefold()


def validate_record(record: MemoryRecord) -> None:
    if len(json.dumps(asdict(record), ensure_ascii=False)) > 10000:
        raise ValueError("Memory record exceeds tool response budget")
    if len(record.id) > 200 or len(record.reason) > 2000:
        raise ValueError("Record ID or reason exceeds limits")
    if record.kind not in {"case", "note", "link", "summary", "architecture_draft"}:
        raise ValueError("Unknown memory kind")
    if not record.title.strip() or len(record.title) > 200 or len(record.text) > 8000:
        raise ValueError("Invalid title or text length")
    if len(record.case_ids) > 20 or len(record.source_update_ids) > 100 or len(record.aliases) > 20:
        raise ValueError("Memory references exceed limits")
    if any(not item.strip() or len(item) > 200 for item in record.aliases):
        raise ValueError("Invalid alias")
    if record.status not in {"active", "inactive"} or (
        record.status == "inactive" and not record.reason.strip()
    ):
        raise ValueError("Inactive records require a reason")
    if record.kind == "note" and record.category not in {
        "proposal",
        "decision",
        "commitment",
        "question",
        "interpretation",
    }:
        raise ValueError("Notes require a category")
    if record.kind != "note" and record.category is not None:
        raise ValueError("Only notes have categories")
    if record.kind != "case" and not record.case_ids:
        raise ValueError("A non-case record requires a case")
    if record.kind != "case" and not record.source_update_ids:
        raise ValueError("Memory must cite source evidence")
    if record.kind == "link" and len(record.source_update_ids) != 1:
        raise ValueError("A link references one precise message version")
