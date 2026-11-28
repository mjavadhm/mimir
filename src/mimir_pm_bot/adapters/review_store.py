"""Durable review requests, publication attempts and Telegram outbox handoff."""

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import uuid4

from mimir_pm_bot.application.reviews import Review, ReviewAction
from mimir_pm_bot.application.work import WorkSource

Transaction = Callable[[], AbstractContextManager[sqlite3.Connection]]


class ReviewStore:
    def __init__(
        self,
        transaction: Transaction,
        source: Callable[[sqlite3.Connection, int], WorkSource],
        repo: str,
        prompt: str,
    ) -> None:
        self.transaction, self.source, self.repo, self.prompt = transaction, source, repo, prompt

    @staticmethod
    def reply(
        db: sqlite3.Connection, pile: int, source: int, key: str, text: str, state: str = "pending"
    ) -> None:
        digest = hashlib.sha256(text.encode()).hexdigest()
        parts = []
        while len(text) > 2000:
            boundary = text.rfind("\n\n", 0, 2001)
            if boundary <= 0:
                boundary = text.rfind("\n", 0, 2001)
            if boundary <= 0:
                boundary = 2000
            parts.append(text[:boundary])
            text = text[boundary:].lstrip("\n")
        if text:
            parts.append(text)
        for part, content in enumerate(parts):
            db.execute(
                """INSERT OR IGNORE INTO outputs
                (pile,target_version,call_key,part,kind,payload,fingerprint,state)
                VALUES(?,?,?,?,'reply',?,?,?)""",
                (pile, source, "review/" + key, part, content, digest, state),
            )

    def stage(self, req: Review | ReviewAction, kind: str) -> str:
        with self.transaction() as db:
            if not db.execute(
                """SELECT 1 FROM pile_members m JOIN piles p ON m.pile=p.id
                WHERE m.update_id=? AND p.id=? AND p.state='active'""",
                (req.source, req.turn),
            ).fetchone():
                raise ValueError("Expired review request")
            evidence = self.source(db, req.source)
            if evidence.edited:
                raise ValueError("Request edited")
            if isinstance(req, Review):
                if isinstance(req.pull, str):
                    match = re.fullmatch(
                        re.escape(f"https://github.com/{self.repo}/pull/") + r"(\d+)/?",
                        req.pull,
                        re.I,
                    )
                    if not match or int(match[1]) <= 0:
                        raise ValueError("PR must belong to the bound repository")
                    pull = int(match[1])
                else:
                    pull = req.pull
                request = json.dumps(
                    {
                        "text": evidence.text,
                        "focus": req.focus,
                        "context": req.context,
                        "publish": req.publish,
                    },
                    ensure_ascii=False,
                )
                db.execute(
                    """INSERT OR IGNORE INTO reviews
                    (pile,source,repo,pull,request,prompt,prompt_hash) VALUES(?,?,?,?,?,?,?)""",
                    (
                        req.turn,
                        req.source,
                        self.repo,
                        pull,
                        request,
                        self.prompt,
                        hashlib.sha256(self.prompt.encode()).hexdigest(),
                    ),
                )
                row = db.execute(
                    "SELECT * FROM reviews WHERE source=? AND pull=?", (req.source, pull)
                ).fetchone()
                if row["request"] != request:
                    raise ValueError("Conflicting review replay")
                self.reply(
                    db,
                    req.turn,
                    req.source,
                    f"start/{row['id']}",
                    f"درخواست بررسی PR #{pull} ثبت شد. شناسهٔ ریویو: {row['id']}",
                    "held",
                )
                if not req.publish:
                    return f"Review {row['id']} queued; start acknowledgement staged"
                ident = row["id"]
            else:
                ident = req.review_id
                row = db.execute(
                    "SELECT * FROM reviews WHERE id=? AND repo=?", (ident, self.repo)
                ).fetchone()
                if not row or (kind == "publish" and row["state"] != "done"):
                    raise ValueError(
                        "Completed review required" if kind == "publish" else "Unknown review"
                    )
            db.execute(
                """INSERT OR IGNORE INTO review_actions(review,pile,source,kind,marker)
                VALUES(?,?,?,?,?)""",
                (ident, req.turn, req.source, kind, f"<!-- mimir-review-{uuid4().hex} -->"),
            )
            return f"Review {ident}: {kind} requested"

    def recover(self) -> None:
        with self.transaction() as db:
            db.execute("UPDATE review_actions SET state='unknown' WHERE state='sending'")

    def next_job(self) -> sqlite3.Row | None:
        with self.transaction() as db:
            row: sqlite3.Row | None = db.execute(
                """SELECT * FROM reviews WHERE repo=?
                AND state IN ('pending','running','ready') AND due<=? ORDER BY id LIMIT 1""",
                (self.repo, time.time()),
            ).fetchone()
            if row and self.source(db, row["source"]).edited:
                db.execute("UPDATE reviews SET state='cancelled' WHERE id=?", (row["id"],))
                return None
            if row and row["state"] == "pending":
                db.execute("UPDATE reviews SET state='running' WHERE id=?", (row["id"],))
            return row

    def running(self, ident: int) -> bool:
        with self.transaction() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM reviews WHERE id=? AND state='running'", (ident,)
                ).fetchone()
            )

    def checkpoint(self, ident: int, base: str, head: str, session: str | None) -> None:
        with self.transaction() as db:
            db.execute(
                """UPDATE reviews SET base=?,head=?,session=? WHERE id=? AND state='running'""",
                (base, head, session, ident),
            )

    def save(self, ident: int, result: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE reviews SET result=?,state='ready' WHERE id=? AND state='running'",
                (result, ident),
            )

    def complete(self, row: sqlite3.Row) -> None:
        with self.transaction() as db:
            if not db.execute(
                "UPDATE reviews SET state='done' WHERE id=? AND state='ready'", (row["id"],)
            ).rowcount:
                return
            self.reply(
                db,
                row["pile"],
                row["source"],
                f"result/{row['id']}",
                f"**ریویو {row['id']} · PR #{row['pull']}** · `{row['head'][:10]}`\n\n"
                + row["result"],
            )

    def retry(
        self,
        row: sqlite3.Row,
        reason: str,
        due: float,
        *,
        reset: bool = False,
        permanent: bool = False,
    ) -> None:
        with self.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM reviews WHERE id=? AND state IN ('ready','running')", (row["id"],)
            ).fetchone():
                return
            if permanent or row["attempt"] >= 3:
                db.execute("UPDATE reviews SET state='failed' WHERE id=?", (row["id"],))
                db.execute(
                    "INSERT INTO incidents(component,reason,pile) VALUES('PR review',?,?)",
                    (reason, row["pile"]),
                )
                self.reply(
                    db,
                    row["pile"],
                    row["source"],
                    f"failure/{row['id']}",
                    f"بررسی PR #{row['pull']} تکمیل نشد. شناسهٔ پیگیری: {row['id']}",
                )
            else:
                db.execute(
                    "UPDATE reviews SET attempt=attempt+1,due=? WHERE id=?", (due, row["id"])
                )
                if reset:
                    db.execute(
                        """UPDATE reviews SET state='pending',result=NULL,session=NULL,
                               base=NULL,head=NULL WHERE id=?""",
                        (row["id"],),
                    )

    def next_action(self) -> sqlite3.Row | None:
        with self.transaction() as db:
            row: sqlite3.Row | None = db.execute(
                """SELECT a.*,r.repo,r.pull,r.base,r.head,r.result,r.source AS review_source,
                r.state AS review_state FROM review_actions a JOIN reviews r ON r.id=a.review
                WHERE r.repo=? AND a.kind='publish' AND a.state IN ('pending','unknown') 
                AND a.due<=?
                AND r.state IN ('done','cancelled','failed') ORDER BY a.id LIMIT 1""",
                (self.repo, time.time()),
            ).fetchone()
            return row

    def begin_post(self, row: sqlite3.Row) -> str:
        with self.transaction() as db:
            if row["review_state"] != "done" or any(
                self.source(db, row[k]).edited for k in ("source", "review_source")
            ):
                return "superseded"
            prior = db.execute(
                """SELECT state,url FROM review_actions WHERE review=? AND id!=?
                AND state IN ('posted','sending','unknown') LIMIT 1""",
                (row["review"], row["id"]),
            ).fetchone()
            if prior:
                return str(prior["url"] or "uncertain")
            db.execute("UPDATE review_actions SET state='sending' WHERE id=?", (row["id"],))
            return "send"

    def settle(
        self,
        row: sqlite3.Row,
        state: str,
        message: str = "",
        url: str | None = None,
        due: float = 0,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE review_actions SET state=?,url=?,due=? WHERE id=?",
                (state, url, due, row["id"]),
            )
            if message:
                self.reply(
                    db, row["pile"], row["source"], f"publication/{row['id']}/{state}", message
                )
            if state in ("failed", "unknown") and row["state"] != state:
                db.execute(
                    "INSERT INTO incidents(component,reason,pile) VALUES('PR comment',?,?)",
                    (state, row["pile"]),
                )
