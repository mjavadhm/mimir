import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine

from mimir_pm_bot.adapters.piles import BatchWindow, enqueue
from mimir_pm_bot.application.destination import SetupChange
from mimir_pm_bot.domain.conversations import Event


class Store:
    def __init__(self, path: Path, window: BatchWindow | None = None) -> None:
        self.path = path
        self.window = window or BatchWindow()

    def initialize(self, bot: int, chat: int, topic: int) -> None:
        if (chat, topic) != (0, 0) and (chat >= 0 or topic <= 0):
            raise ValueError("Configure both a negative group ID and a positive topic ID")
        with closing(sqlite3.connect(self.path)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "binding" not in tables:
                raise ValueError("Choose a new Mimir database")
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise RuntimeError("WAL unavailable")
        engine = create_engine(URL.create("sqlite", database=str(self.path)))
        config = Config()
        config.set_main_option("script_location", "mimir_pm_bot:migrations")
        config.attributes["engine"] = engine
        try:
            command.upgrade(config, "head")
        finally:
            engine.dispose()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO binding(singleton,bot,chat,topic) VALUES(1,?,?,?)",
                (bot, chat, topic),
            )
            owner, old_chat, old_topic, managed = db.execute(
                "SELECT bot,chat,topic,destination_managed FROM binding"
            ).fetchone()
            if owner != bot or (chat and not managed and (old_chat, old_topic) != (chat, topic)):
                raise ValueError("Database belongs to a different bot or topic")

    def checkpoint(self) -> int | None:
        with closing(sqlite3.connect(self.path)) as db:
            offset, arrived = db.execute("SELECT next_offset,last_arrival FROM binding").fetchone()
            # Telegram may choose a random update ID after a week without updates.
            if arrived and datetime.now(UTC) - datetime.fromisoformat(arrived) < timedelta(days=6):
                return int(offset)
            return None

    def reserve_probe(self, update_id: int) -> bool:
        with closing(sqlite3.connect(self.path)) as db, db:
            return bool(
                db.execute(
                    "INSERT OR IGNORE INTO destination_probes VALUES(?, 'unknown')", (update_id,)
                ).rowcount
            )

    def destination(self) -> tuple[int, int]:
        with closing(sqlite3.connect(self.path)) as db:
            chat, topic = db.execute("SELECT chat,topic FROM binding").fetchone()
            return int(chat), int(topic)

    def setup(self, actor: int) -> tuple[bool, int | None]:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT proposed_chat FROM destination_setup WHERE actor=?", (actor,)
            ).fetchone()
            return (True, row[0]) if row else (False, None)

    def record(self, event: Event, change: SetupChange | None = None) -> int:
        v = event.version
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            inserted = db.execute(
                "INSERT OR IGNORE INTO inbox VALUES(?,?,?)",
                (event.update_id, event.arrived.isoformat(), "stored" if v else "ignored"),
            )
            if inserted.rowcount:
                if change:
                    if change.step == "begin":
                        db.execute(
                            "INSERT OR REPLACE INTO destination_setup VALUES(?,NULL)",
                            (change.actor,),
                        )
                    elif change.step == "group":
                        db.execute(
                            "UPDATE destination_setup SET proposed_chat=? WHERE actor=?",
                            (change.group, change.actor),
                        )
                    elif change.step == "abort":
                        db.execute("DELETE FROM destination_setup WHERE actor=?", (change.actor,))
                    elif db.execute(
                        "SELECT 1 FROM destination_setup WHERE actor=? AND proposed_chat=?",
                        (change.actor, change.group),
                    ).fetchone():
                        db.execute(
                            "UPDATE binding SET chat=?,topic=?,destination_managed=1",
                            (change.group, change.topic),
                        )
                        db.execute("DELETE FROM destination_setup")
                        db.execute(
                            "UPDATE destination_probes SET state='confirmed' WHERE update_id=?",
                            (event.update_id,),
                        )
                if event.admin:
                    db.execute(
                        """INSERT INTO admin_commands(update_id,actor,command,target_pile)
                        VALUES(?,?,?,(SELECT id FROM piles WHERE state='active'))""",
                        (event.update_id, *event.admin),
                    )
                if v:
                    db.execute(
                        "INSERT OR IGNORE INTO messages VALUES(?,?,?)", (v.chat, v.message, v.topic)
                    )
                    db.execute(
                        "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            event.update_id,
                            v.chat,
                            v.message,
                            v.speaker_kind,
                            v.speaker_id,
                            v.speaker_name,
                            v.text,
                            v.caption,
                            v.sent.isoformat(),
                            v.edited.isoformat() if v.edited else None,
                            v.reply_to,
                            v.is_edit,
                        ),
                    )
                    enqueue(db, event.update_id, event.arrived, self.window)
                db.execute(
                    "UPDATE binding SET next_offset=?,last_arrival=?",
                    (event.update_id + 1, event.arrived.isoformat()),
                )
            return int(db.execute("SELECT next_offset FROM binding").fetchone()[0])
