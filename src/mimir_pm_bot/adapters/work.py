"""SQLite-backed issue coordination and durable GitHub delivery."""

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any
from uuid import uuid4

from mimir_pm_bot.adapters.github import GitHubError
from mimir_pm_bot.application.memory import Request
from mimir_pm_bot.application.work import (
    TOOLS,
    Decision,
    IssueTracker,
    Link,
    Prepare,
    Start,
    Status,
    Submit,
    WorkSource,
)

Transaction = Callable[[], AbstractContextManager[sqlite3.Connection]]


def finish_work(db: sqlite3.Connection, turn: int, success: bool) -> None:
    if success:
        db.execute(
            "UPDATE issue_actions SET state='pending' WHERE pile=? AND state='held'", (turn,)
        )
    else:
        db.execute("DELETE FROM issue_actions WHERE pile=? AND state='held'", (turn,))
        db.execute("DELETE FROM issue_drafts WHERE pile=?", (turn,))
        db.execute("DELETE FROM work_events WHERE pile=?", (turn,))


class Work:
    def __init__(
        self,
        transaction: Transaction,
        github: IssueTracker,
        decision: Callable[[sqlite3.Connection, str, int], Decision],
        source: Callable[[sqlite3.Connection, int], WorkSource],
    ) -> None:
        self.transaction, self.github = transaction, github
        self.decision, self.source = decision, source

    def bind(self) -> None:
        with self.transaction() as db:
            if not db.execute(
                "UPDATE binding SET github_repo=? WHERE github_repo IS NULL OR github_repo=?",
                (self.github.repo, self.github.repo),
            ).rowcount:
                raise ValueError("Database belongs to another GitHub repository")

    def check_source(self, db: sqlite3.Connection, turn: int, source: int) -> None:
        if (
            not db.execute(
                "SELECT 1 FROM pile_members WHERE pile=? AND update_id=?", (turn, source)
            ).fetchone()
            or self.source(db, source).edited
        ):
            raise ValueError("Source expired or belongs to another turn")

    def prepare(self, db: sqlite3.Connection, req: Prepare) -> dict[str, object]:
        decision = self.decision(db, req.decision, req.decision_revision)
        current = db.execute(
            "SELECT * FROM issue_drafts WHERE decision=? ORDER BY revision DESC LIMIT 1",
            (req.decision,),
        ).fetchone()
        body = "\n\n".join(
            filter(
                None, (req.description, decision.text, decision.reason, "\n".join(decision.links))
            )
        )
        if current and (current["title"], current["body"], current["decision_revision"]) == (
            req.title,
            body,
            req.decision_revision,
        ):
            return dict(current)
        if req.expected_revision != (current["revision"] if current else 0):
            raise ValueError("Draft changed")
        draft = current["draft"] if current else uuid4().hex
        action = db.execute("SELECT state FROM issue_actions WHERE draft=?", (draft,)).fetchone()
        if action:
            if action["state"] != "failed":
                raise ValueError("Draft already submitted")
            db.execute("DELETE FROM issue_actions WHERE draft=?", (draft,))
        revision = req.expected_revision + 1
        db.execute(
            "INSERT INTO issue_drafts VALUES(?,?,?,?,?,?,?,?)",
            (
                draft,
                revision,
                req.decision,
                decision.revision,
                decision.subject,
                req.title,
                body,
                req.turn,
            ),
        )
        return {"draft": draft, "revision": revision, "title": req.title, "body": body}

    def validate(self, db: sqlite3.Connection, draft: str, revision: int) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM issue_drafts WHERE draft=? ORDER BY revision DESC LIMIT 1", (draft,)
        ).fetchone()
        if not row or row["revision"] != revision:
            raise ValueError("Draft revision changed")
        self.decision(db, row["decision"], row["decision_revision"])
        return row

    def local(self, req: Request) -> dict[str, Any]:
        with self.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM piles WHERE id=? AND state='active'", (req.turn,)
            ).fetchone():
                raise ValueError("Expired turn")
            if isinstance(req, Prepare):
                return self.prepare(db, req)
            if isinstance(req, Submit):
                self.check_source(db, req.turn, req.source)
                self.validate(db, req.draft, req.revision)
                db.execute(
                    """INSERT OR IGNORE INTO issue_actions
                    (draft,revision,marker,pile,source,state) VALUES(?,?,?,?,?,'held')""",
                    (
                        req.draft,
                        req.revision,
                        f"<!-- mimir:{uuid4().hex} -->",
                        req.turn,
                        req.source,
                    ),
                )
                return dict(
                    db.execute(
                        "SELECT revision,state,number FROM issue_actions WHERE draft=?",
                        (req.draft,),
                    ).fetchone()
                )
            if not isinstance(req, Status):
                raise ValueError("Unsupported request")
            row = db.execute(
                """SELECT d.subject FROM issue_actions a JOIN issue_drafts d
                USING(draft,revision) WHERE a.number=?""",
                (req.issue,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown managed issue")
            if isinstance(req, Start):
                self.check_source(db, req.turn, req.source)
                if isinstance(req, Link):
                    source = self.source(db, req.source)
                    text = source.text
                    url = f"https://github.com/{self.github.repo}/pull/{req.pull}"
                    if not re.search(re.escape(url) + r"(?=$|[\s/#?.,)])", text, re.I):
                        raise ValueError("The source must link the bound repository PR")
                db.execute(
                    "INSERT OR IGNORE INTO work_events VALUES(?,?,?,?)",
                    (req.issue, req.turn, req.source, req.pull if isinstance(req, Link) else 0),
                )
            events = []
            pulls = set()
            for event in db.execute(
                """SELECT w.* FROM work_events w JOIN issue_actions a
                ON a.number=w.issue JOIN issue_drafts d USING(draft,revision)
                WHERE d.subject=? ORDER BY w.source""",
                (row["subject"],),
            ):
                evidence = self.source(db, event["source"])
                if event["pull"] and event["issue"] == req.issue and not evidence.edited:
                    pulls.add(event["pull"])
                events.append(
                    {
                        "issue": event["issue"],
                        "pull": event["pull"],
                        "speaker": evidence.speaker,
                        "speaker_id": evidence.speaker_id,
                        "source": evidence.link,
                        "excerpt": evidence.text,
                        "edited_since": evidence.edited,
                    }
                )
            return {"events": events, "pulls": sorted(pulls)}

    async def invoke(self, name: str, args: dict[str, Any]) -> str:
        req = TOOLS[name][0].model_validate(args)
        if isinstance(req, Link):
            await self.github.metadata(req.pull, True)
        result = await asyncio.to_thread(self.local, req)
        if type(req) is Status:
            issue_metadata = await self.github.metadata(req.issue)
            metadata = {
                "issue": issue_metadata,
                "pulls": [await self.github.metadata(p, True) for p in result["pulls"]],
            }

            related = {event["issue"] for event in result["events"] if event["issue"] != req.issue}
            related_states = {
                issue: (await self.github.metadata(issue))["state"] for issue in sorted(related)
            }
            related_states[req.issue] = issue_metadata["state"]
            result["active_claims"] = [
                e
                for e in result["events"]
                if not e["pull"] and not e["edited_since"] and related_states[e["issue"]] == "open"
            ]

            def save() -> None:
                with self.transaction() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO work_observations VALUES(?,?,?)",
                        (req.issue, time.time(), json.dumps(metadata)),
                    )

            await asyncio.to_thread(save)
            result["github"] = metadata
        return json.dumps(result, ensure_ascii=False)

    def claim(self) -> sqlite3.Row | None:
        with self.transaction() as db:
            row: sqlite3.Row | None = db.execute(
                """SELECT a.*,d.title,d.body FROM issue_actions a JOIN issue_drafts d
                USING(draft,revision) WHERE state IN ('pending','unknown') AND due<=?
                ORDER BY due,a.rowid LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if row and row["state"] == "pending":
                try:
                    self.validate(db, row["draft"], row["revision"])
                    self.check_source(db, row["pile"], row["source"])
                except ValueError as exc:
                    self.record(db, row, "failed", str(exc))
                    return None
                db.execute(
                    "UPDATE issue_actions SET state='sending' WHERE draft=?", (row["draft"],)
                )
            return row

    def record(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        state: str,
        detail: str,
        number: int | None = None,
        due: float = 0,
    ) -> None:
        db.execute(
            "UPDATE issue_actions SET state=?,result=?,number=?,due=? WHERE draft=?",
            (state, detail, number, due, row["draft"]),
        )
        if state in ("sent", "failed", "unknown"):
            text = (
                f"https://github.com/{self.github.repo}/issues/{number}"
                if number
                else f"وضعیت ساخت ایشو: {state} — {detail}"
            )
            db.execute(
                """INSERT OR IGNORE INTO outputs(pile,call_key,part,kind,payload,fingerprint)
                VALUES(?,?,0,'send',?,?)""",
                (
                    row["pile"],
                    "github/" + row["marker"] + state,
                    text,
                    hashlib.sha256(text.encode()).hexdigest(),
                ),
            )

    async def deliver_one(self) -> bool:
        row = await asyncio.to_thread(self.claim)
        if not row:
            return False
        state, detail, due, number = "sent", "GitHub receipt", 0.0, None
        try:
            if row["state"] == "unknown":
                number = await self.github.reconcile(row["marker"])
                if number is None:
                    state, detail, due = "unknown", "No unique verified receipt", time.time() + 300
            else:
                number = await self.github.create(
                    row["title"], row["body"] + "\n\n" + row["marker"]
                )
        except GitHubError as exc:
            state = "unknown" if row["state"] == "unknown" else exc.state
            detail, due = str(exc), max(exc.due, time.time() + 60)

        def settle() -> None:
            with self.transaction() as db:
                self.record(db, row, state, detail, number, due)

        await asyncio.to_thread(settle)
        return True

    async def delivery(self, interval: float) -> None:
        while True:
            await self.deliver_one()
            await asyncio.sleep(interval)

    def review_context(self, pull: int) -> list[dict[str, Any]]:
        with self.transaction() as db:
            rows = db.execute(
                """SELECT DISTINCT e.issue,d.title,d.body,d.decision,d.decision_revision
                FROM work_events e JOIN issue_actions a ON a.number=e.issue
                JOIN issue_drafts d ON d.draft=a.draft AND d.revision=a.revision
                WHERE e.pull=? ORDER BY e.issue""",
                (pull,),
            )
            context = []
            for row in rows:
                issue = dict(row)
                try:
                    self.decision(db, row["decision"], row["decision_revision"])
                    issue["decision_current"] = True
                except ValueError:
                    issue["decision_current"] = False
                context.append(issue)
            return context
