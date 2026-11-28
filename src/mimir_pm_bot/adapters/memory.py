"""Versioned subject memory. Transaction ownership belongs to the calling use case."""

import asyncio
import json
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mimir_pm_bot.application.memory import Read, Request, Search, Sources, Stage
from mimir_pm_bot.application.work import Decision, WorkSource


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").split())


def evidence(db: sqlite3.Connection, version: int) -> dict[str, Any]:
    row = db.execute(
        """SELECT v.*,m.topic FROM versions v JOIN messages m USING(chat,message)
        WHERE v.update_id=?""",
        (version,),
    ).fetchone()
    if row is None:
        raise ValueError("Unknown source version")
    latest = db.execute(
        """SELECT v.update_id FROM versions v JOIN inbox i USING(update_id)
        WHERE chat=? AND message=? ORDER BY arrived DESC,v.update_id DESC LIMIT 1""",
        (row["chat"], row["message"]),
    ).fetchone()[0]
    chat = str(row["chat"])
    return {
        **dict(row),
        "latest_version": latest,
        "edited_since": latest != version,
        "link": f"https://t.me/c/{chat[4:]}/{row['topic']}/{row['message']}"
        if chat.startswith("-100")
        else None,
    }


def discard(db: sqlite3.Connection, turn: int) -> None:
    db.execute("DELETE FROM decision_versions WHERE pile=? AND committed_at IS NULL", (turn,))
    db.execute("DELETE FROM subject_openings WHERE pile=? AND committed_at IS NULL", (turn,))
    db.execute("""DELETE FROM subjects WHERE id NOT IN
        (SELECT subject FROM decision_versions UNION SELECT subject FROM subject_openings)""")
    db.execute(
        "UPDATE outputs SET state='failed',result='memory discarded' WHERE pile=? AND state='held'",
        (turn,),
    )


def finalize(db: sqlite3.Connection, turn: int, success: bool) -> bool:
    """Publish memory and release its explicit notifications in the turn-finish transaction."""
    if success:
        refs = db.execute(
            """SELECT s.source FROM decision_sources s JOIN decision_versions d
            ON d.id=s.entry WHERE d.pile=? AND d.committed_at IS NULL UNION
            SELECT j.value FROM subject_openings o,json_each(o.request,'$.sources') j
            WHERE o.pile=? AND o.committed_at IS NULL""",
            (turn, turn),
        )
        stale_revision = db.execute(
            """SELECT drafts.decision FROM decision_versions drafts
            WHERE drafts.pile=? AND drafts.committed_at IS NULL GROUP BY drafts.decision
            HAVING MIN(drafts.revision) != 1+COALESCE((SELECT MAX(previous.revision)
            FROM decision_versions previous WHERE previous.decision=drafts.decision
            AND previous.committed_at IS NOT NULL),0) LIMIT 1""",
            (turn,),
        ).fetchone()
        success = not stale_revision and all(not evidence(db, r[0])["edited_since"] for r in refs)
    if not success:
        discard(db, turn)
        return False
    db.execute(
        "UPDATE decision_versions SET committed_at=? WHERE pile=? AND committed_at IS NULL",
        (datetime.now(UTC).isoformat(), turn),
    )
    db.execute(
        "UPDATE subject_openings SET committed_at=? WHERE pile=? AND committed_at IS NULL",
        (datetime.now(UTC).isoformat(), turn),
    )
    db.execute("UPDATE outputs SET state='pending' WHERE pile=? AND state='held'", (turn,))
    return True


def stage(db: sqlite3.Connection, req: Stage) -> dict[str, Any]:
    encoded = req.model_dump_json()
    opening = db.execute(
        "SELECT * FROM subject_openings WHERE pile=? AND operation=?", (req.turn, req.operation)
    ).fetchone()
    if opening:
        if opening["request"] != encoded:
            raise ValueError("Operation reused with different arguments")
        return {"subject_id": opening["subject"], "staged": True}
    prior = db.execute(
        "SELECT * FROM decision_versions WHERE pile=? AND operation=?", (req.turn, req.operation)
    ).fetchone()
    if prior:
        if encoded != prior["request"]:
            raise ValueError("Operation reused with different arguments")
        return {
            "subject_id": prior["subject"],
            "decision": prior["decision"],
            "revision": prior["revision"],
            "staged": True,
        }
    if bool(req.decision) != bool(req.expected_revision):
        raise ValueError("Existing decision and expected revision must be supplied together")
    if req.status == "inactive" and (not req.decision or not req.reason):
        raise ValueError("Retirement requires existing decision and reason")
    for source in req.sources:
        if evidence(db, source)["edited_since"]:
            raise ValueError("Source has been edited")
    title = normalized(req.subject)
    if not title:
        raise ValueError("Missing subject")
    if req.decision:
        current = db.execute(
            """SELECT d.revision,s.title_key FROM decision_versions d JOIN
            subjects s ON s.id=d.subject WHERE decision=? AND (pile=? OR committed_at IS NOT NULL)
            ORDER BY revision DESC LIMIT 1""",
            (req.decision, req.turn),
        ).fetchone()
        if (
            not current
            or current["revision"] != req.expected_revision
            or current["title_key"] != title
        ):
            raise ValueError("Stale revision or subject")
    db.execute("INSERT OR IGNORE INTO subjects(title,title_key) VALUES(?,?)", (req.subject, title))
    subject = db.execute("SELECT id FROM subjects WHERE title_key=?", (title,)).fetchone()[0]
    if req.entity == "subject":
        if (
            req.expected_revision
            or req.decision
            or req.text
            or req.reason
            or req.status != "active"
        ):
            raise ValueError("Subject opening expects a title and sources")
        if db.execute(
            "SELECT 1 FROM subject_openings WHERE subject=? UNION ALL "
            "SELECT 1 FROM decision_versions WHERE subject=?",
            (subject, subject),
        ).fetchone():
            raise ValueError("Subject already exists")
        db.execute(
            "INSERT INTO subject_openings(subject,pile,operation,request) VALUES(?,?,?,?)",
            (subject, req.turn, req.operation, encoded),
        )
        return {"subject_id": subject, "staged": True}
    if not req.text:
        raise ValueError("Decision text required")
    decision = req.decision or uuid4().hex
    rev = req.expected_revision + 1
    entry = db.execute(
        """INSERT INTO decision_versions(decision,revision,subject,body,reason,status,
        pile,operation,request) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            decision,
            rev,
            subject,
            req.text,
            req.reason,
            req.status,
            req.turn,
            req.operation,
            encoded,
        ),
    ).lastrowid
    db.executemany(
        "INSERT INTO decision_sources VALUES(?,?)", [(entry, s) for s in set(req.sources)]
    )
    return {"subject_id": subject, "decision": decision, "revision": rev, "staged": True}


VISIBLE = """SELECT d.id,d.decision,d.revision,d.subject AS subject_id,s.title,d.body AS text,
    d.reason,d.status,d.pile AS turn,d.committed_at FROM decision_versions d JOIN subjects s
    ON d.subject=s.id WHERE (d.committed_at IS NOT NULL OR d.pile=?)"""


def run(db: sqlite3.Connection, req: Request) -> object:
    if not db.execute("SELECT 1 FROM piles WHERE id=? AND state='active'", (req.turn,)).fetchone():
        raise ValueError("Expired turn")
    if isinstance(req, Stage):
        return stage(db, req)
    db.execute(
        """INSERT INTO memory_reads VALUES(?,?,1) ON CONFLICT(pile,tool)
        DO UPDATE SET count=count+1""",
        (req.turn, type(req).__name__),
    )
    if isinstance(req, Sources):
        records = []
        for version in req.versions:
            old = evidence(db, version)
            records.append(old)
            if old["edited_since"]:
                records.append(evidence(db, old["latest_version"]))
        return records
    if isinstance(req, Search):
        query = normalized(req.query)
        db.create_function("memory_key", 1, normalized)
        decisions = [
            dict(r)
            for r in db.execute(
                "WITH visible AS ("
                + VISIBLE
                + """)
            SELECT * FROM visible v WHERE status='active' AND NOT EXISTS
            (SELECT 1 FROM visible newer WHERE newer.decision=v.decision
             AND newer.revision>v.revision)
            AND (instr(memory_key(title),?) OR instr(memory_key(text),?))
            ORDER BY id DESC LIMIT 30""",
                (req.turn, query, query),
            )
        ]
        subjects = db.execute(
            """SELECT s.id AS subject_id,s.title FROM subjects s
            JOIN subject_openings o ON o.subject=s.id WHERE (o.pile=? OR o.committed_at IS NOT NULL)
            AND NOT EXISTS(SELECT 1 FROM decision_versions d WHERE d.subject=s.id
              AND (d.pile=? OR d.committed_at IS NOT NULL))
            AND instr(s.title_key,?) ORDER BY s.id DESC LIMIT 30""",
            (req.turn, req.turn, query),
        )
        return (decisions + [dict(r) for r in subjects])[:30]
    if isinstance(req, Read):
        if bool(req.subject_id) == bool(req.decision):
            raise ValueError("Specify exactly one decision or subject_id")
        where, reference = (
            ("subject_id", req.subject_id) if req.subject_id else ("decision", req.decision)
        )
        query = "WITH visible AS (" + VISIBLE + f") SELECT * FROM visible v WHERE {where}=?"
        if not req.history:
            query += """ AND NOT EXISTS (SELECT 1 FROM visible newer WHERE
                newer.decision=v.decision AND newer.revision>v.revision)"""
        rows = db.execute(query + " ORDER BY decision,revision", (req.turn, reference))
        result = []
        for r in rows:
            refs = db.execute(
                "SELECT source FROM decision_sources WHERE entry=? ORDER BY source", (r["id"],)
            )
            result.append({**dict(r), "sources": [evidence(db, s[0]) for s in refs]})
        if not result and req.subject_id:
            opening = db.execute(
                """SELECT s.id,s.title,o.request FROM subjects s JOIN
                subject_openings o ON s.id=o.subject WHERE s.id=?
                AND (o.committed_at IS NOT NULL OR o.pile=?)""",
                (req.subject_id, req.turn),
            ).fetchone()
            if opening:
                return {
                    "subject_id": opening["id"],
                    "title": opening["title"],
                    "decisions": [],
                    "sources": [evidence(db, i) for i in json.loads(opening["request"])["sources"]],
                }
        return result
    raise ValueError("Unsupported tool")


TOOLS: dict[str, tuple[type[Request], str]] = {
    "memory_search": (Search, "Search current active decisions by subject title or decision text."),
    "memory_read": (
        Read,
        "Read a decision or subject by decision or subject_id, with sources. "
        "Set history=true for past revisions.",
    ),
    "memory_sources": (Sources, "Read exact Telegram update versions and later edits."),
    "memory_stage": (
        Stage,
        "Stage a decision (text required) or open a subject (entity=subject, omit text). "
        "Create with expected_revision=0; "
        "to revise pass decision ID and latest revision. sources are exact update IDs.",
    ),
}


def memory_schemas() -> list[dict[str, Any]]:
    return [
        {"name": k, "description": desc, "inputSchema": model.model_json_schema()}
        for k, (model, desc) in TOOLS.items()
    ]


class Memory:
    def __init__(
        self, transaction: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self.transaction = transaction

    async def invoke(self, name: str, args: dict[str, Any]) -> str:
        model, _ = TOOLS[name]
        req = model.model_validate(args)

        def execute() -> str:
            with self.transaction() as db:
                return json.dumps(run(db, req), ensure_ascii=False)

        return await asyncio.to_thread(execute)


def committed_decision(db: sqlite3.Connection, decision: str, revision: int) -> Decision:
    row = db.execute(
        """SELECT * FROM decision_versions WHERE decision=? AND committed_at IS NOT NULL
        ORDER BY revision DESC LIMIT 1""",
        (decision,),
    ).fetchone()
    if not row or row["status"] != "active" or row["revision"] != revision:
        raise ValueError("Decision changed")
    sources = [
        evidence(db, r[0])
        for r in db.execute(
            "SELECT source FROM decision_sources WHERE entry=? ORDER BY source", (row["id"],)
        )
    ]
    if not sources or any(s["edited_since"] or not s["link"] for s in sources):
        raise ValueError("Decision evidence changed")
    return Decision(
        row["subject"], revision, row["body"], row["reason"], tuple(s["link"] for s in sources)
    )


def work_source(db: sqlite3.Connection, version: int) -> WorkSource:
    source = evidence(db, version)
    return WorkSource(
        source["speaker_name"],
        source["speaker_id"],
        source["text"] or source["caption"] or "",
        source["link"],
        source["edited_since"],
    )
