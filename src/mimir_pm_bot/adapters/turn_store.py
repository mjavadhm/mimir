import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from mimir_pm_bot.adapters.memory import discard, finalize
from mimir_pm_bot.adapters.work import finish_work
from mimir_pm_bot.application.turns import Control, Output, Turn
from mimir_pm_bot.profiles import read_prompt


class Turns:
    def __init__(self, path: Path, instructions: str | None = None) -> None:
        self.path = path
        self.instructions = instructions if instructions is not None else read_prompt("mimir")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db

    def recover(self) -> None:
        with self.transaction() as db:
            db.execute("UPDATE incidents SET state='unknown' WHERE state='sending'")
            db.execute("""UPDATE turn_cards SET state=CASE WHEN message IS NULL
                THEN 'unknown' ELSE 'ready' END WHERE state='sending'""")
            for row in db.execute("SELECT id FROM piles WHERE state='active'").fetchall():
                finish_work(db, row[0], False)
                discard(db, row[0])
            db.execute(
                "UPDATE piles SET state='unknown',detail='service interrupted' WHERE state='active'"
            )
            db.execute("UPDATE issue_actions SET state='unknown' WHERE state='sending'")
            db.execute("UPDATE outputs SET state='unknown' WHERE state='sending'")
            # The project lock is also held by the ACP child. Recovery runs only after
            # the previous owner has exited. Keep its unknown outcome for inspection.
            db.execute("""UPDATE outputs SET state='failed', result='service interrupted'
                WHERE state='pending' AND pile IN (SELECT id FROM piles
                WHERE state='unknown' AND detail='service interrupted')""")

    def claim(self, cap: int) -> Turn | None:
        with self.transaction() as db:
            if db.execute(
                "SELECT 1 FROM piles WHERE state='active' OR "
                "(state='unknown' AND detail IS NOT 'service interrupted') LIMIT 1"
            ).fetchone():
                return None
            p = db.execute(
                "SELECT id,due FROM piles WHERE state='waiting' ORDER BY id LIMIT 1"
            ).fetchone()
            if not p or datetime.fromisoformat(p["due"]) > datetime.now(UTC):
                return None
            lines = db.execute(
                """SELECT v.update_id AS version,v.message,v.speaker_kind,
               v.speaker_id,v.speaker_name,v.text,v.caption,v.sent,v.edited,v.is_edit,v.reply_to,
               COALESCE((SELECT substr(COALESCE(parent.text,parent.caption),1,1500)
                 FROM versions parent JOIN inbox pi ON pi.update_id=parent.update_id
                 WHERE parent.chat=v.chat AND parent.message=v.reply_to
                 ORDER BY pi.arrived DESC,parent.update_id DESC LIMIT 1),
                 (SELECT substr(o.payload,1,1500) FROM outputs o WHERE o.state='sent'
                  AND o.pile IN (SELECT id FROM piles WHERE chat=v.chat AND topic=
                    (SELECT topic FROM messages WHERE chat=v.chat AND message=v.message))
                  AND o.result=CAST(v.reply_to AS TEXT) ORDER BY o.id DESC LIMIT 1)) AS reply_text
               FROM pile_members pm JOIN versions v USING(update_id)
               JOIN inbox i USING(update_id) WHERE pile=? ORDER BY i.arrived,v.update_id""",
                (p["id"],),
            )
            prompt = (
                self.instructions
                + f"Turn {p['id']}\n"
                + json.dumps([dict(r) for r in lines], ensure_ascii=False)
            )
            if len(prompt.encode()) > cap:
                db.execute(
                    "UPDATE piles SET detail='prompt exceeds measured byte budget' WHERE id=?",
                    (p["id"],),
                )
                return None
            db.execute("UPDATE piles SET state='active',detail=NULL WHERE id=?", (p["id"],))
            return Turn(p["id"], prompt)

    def finish(self, pile: int, state: str, detail: str) -> str:
        with self.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM piles WHERE id=? AND state='active'", (pile,)
            ).fetchone():
                return "inactive turn"
            success = state == "done" and detail == "end_turn"
            if not finalize(db, pile, success) and success:
                detail = "memory conflict"
            finish_work(db, pile, success and detail == "end_turn")
            db.execute(
                "UPDATE piles SET state=?,detail=? WHERE id=? AND state='active'",
                (state, detail, pile),
            )
            return detail

    def status(self) -> str:
        with self.transaction() as db:
            rows = db.execute("SELECT state,count(*) n FROM piles GROUP BY state").fetchall()
            reason = db.execute(
                "SELECT detail FROM piles WHERE state IN ('unknown','waiting') "
                "AND detail IS NOT NULL LIMIT 1"
            ).fetchone()
            unknown = db.execute("SELECT count(*) FROM outputs WHERE state='unknown'").fetchone()[0]
            return (
                ", ".join(f"{r['state']}={r['n']}" for r in rows)
                + (f"; uncertain sends={unknown}" if unknown else "")
                + (f" ({reason[0]})" if reason else "")
            )

    def control(self) -> Control | None:
        with self.transaction() as db:
            r = db.execute(
                "SELECT * FROM admin_commands WHERE claimed=0 ORDER BY update_id LIMIT 1"
            ).fetchone()
            if r:
                db.execute(
                    "UPDATE admin_commands SET claimed=1 WHERE update_id=?", (r["update_id"],)
                )
                return Control(r["actor"], r["command"], r["target_pile"])
            return None

    def request_model(self, model: str | None) -> None:
        with self.transaction() as db:
            db.execute("UPDATE binding SET pending_model=?", (model,))

    def pending_model(self) -> str | None:
        with self.transaction() as db:
            model = db.execute("SELECT pending_model FROM binding").fetchone()[0]
            return str(model) if model else None

    def receipt(self, pile: int, key: str, request_hash: str) -> str | None:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT request_hash,state FROM outputs WHERE pile=? AND call_key=? ORDER BY part",
                (pile, key),
            ).fetchall()
            if rows and rows[0]["request_hash"]:
                if rows[0]["request_hash"] != request_hash:
                    raise ValueError("Call key reused with different request")
                return ", ".join(r["state"] for r in rows)
            return None

    def stage(
        self,
        pile: int,
        key: str,
        kind: str,
        parts: list[str],
        version: int | None,
        *,
        allow_new: bool = True,
        request_hash: str | None = None,
    ) -> str:
        fingerprint = hashlib.sha256(json.dumps([kind, version, parts]).encode()).hexdigest()
        with self.transaction() as db:
            prior = db.execute(
                "SELECT fingerprint,state FROM outputs WHERE pile=? AND call_key=? ORDER BY part",
                (pile, key),
            ).fetchall()
            if prior:
                if prior[0][0] != fingerprint:
                    raise ValueError("Call key reused with different request")
                return ", ".join(r[1] for r in prior)
            if (
                not allow_new
                or not db.execute(
                    "SELECT 1 FROM piles WHERE id=? AND state='active'", (pile,)
                ).fetchone()
            ):
                raise ValueError("Expired turn")
            if (
                version is not None
                and not db.execute(
                    """SELECT 1 FROM versions v JOIN messages m USING(chat,message)
                    JOIN piles p ON p.chat=m.chat AND p.topic=m.topic WHERE v.update_id=?
                    AND p.id=?""",
                    (version, pile),
                ).fetchone()
            ):
                raise ValueError("Unbound version")
            held = (
                kind in {"send", "reply"}
                and db.execute(
                    "SELECT 1 FROM decision_versions WHERE pile=? AND committed_at IS NULL "
                    "UNION ALL SELECT 1 FROM subject_openings WHERE pile=? "
                    "AND committed_at IS NULL UNION ALL SELECT 1 FROM issue_drafts WHERE pile=? "
                    "UNION ALL SELECT 1 FROM work_events WHERE pile=? "
                    "UNION ALL SELECT 1 FROM issue_actions WHERE pile=? AND state='held' LIMIT 1",
                    (pile, pile, pile, pile, pile),
                ).fetchone()
                is not None
            )
            delivery_state = "held" if held else "pending"
            db.executemany(
                """INSERT INTO outputs(pile,call_key,part,kind,payload,fingerprint,
                            target_version,request_hash,state)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                [
                    (pile, key, i, kind, part, fingerprint, version, request_hash, delivery_state)
                    for i, part in enumerate(parts)
                ],
            )
            return delivery_state

    def next_output(self) -> Output | None:
        with self.transaction() as db:
            r = db.execute(
                "SELECT * FROM outputs WHERE state='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if not r or r["due"] > time.time():
                return None
            prev = db.execute(
                """SELECT 1 FROM outputs WHERE pile=? AND call_key=? AND part<?
                AND state!='sent' LIMIT 1""",
                (r["pile"], r["call_key"], r["part"]),
            ).fetchone()
            target = None
            version = r["target_version"]
            if version is not None:
                latest = db.execute(
                    """
                    SELECT v.update_id, v.message FROM versions orig
                    JOIN versions v ON v.chat=orig.chat AND v.message=orig.message
                    JOIN inbox i ON i.update_id=v.update_id
                    WHERE orig.update_id=? ORDER BY i.arrived DESC,v.update_id DESC LIMIT 1""",
                    (version,),
                ).fetchone()
                if latest and latest["update_id"] == version:
                    target = int(latest["message"])
            if prev or (version is not None and target is None):
                db.execute(
                    "UPDATE outputs SET state='failed',result='stale or earlier part failed' "
                    "WHERE id=?",
                    (r["id"],),
                )
                return None
            owner = db.execute(
                "SELECT state,detail,chat,topic FROM piles WHERE id=?", (r["pile"],)
            ).fetchone()
            if owner["state"] == "unknown":
                return None
            if owner["state"] == "done" and owner["detail"] != "end_turn":
                db.execute(
                    "UPDATE outputs SET state='failed',result='turn stopped' WHERE id=?", (r["id"],)
                )
                return None
            db.execute(
                "UPDATE outputs SET state='sending',attempts=attempts+1 WHERE id=?", (r["id"],)
            )
            return Output(
                r["id"],
                r["kind"],
                r["payload"],
                target,
                r["attempts"] + 1,
                owner["chat"],
                owner["topic"],
            )

    def settle(self, output: int, state: str, result: str, due: float = 0) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE outputs SET state=?,result=?,due=? WHERE id=? AND state='sending'",
                (state, result, due, output),
            )
