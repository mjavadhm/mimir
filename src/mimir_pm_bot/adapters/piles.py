import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class BatchWindow:
    quiet: timedelta = timedelta(seconds=4)
    ceiling: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        if not timedelta() < self.quiet <= self.ceiling:
            raise ValueError("Require 0 < quiet <= ceiling")


def enqueue(db: sqlite3.Connection, update: int, arrival: datetime, window: BatchWindow) -> None:
    """Join the newest still-open pile within the caller's inbox transaction."""
    arrival = arrival.astimezone(UTC)
    route = db.execute("SELECT chat,topic FROM binding").fetchone()
    latest = db.execute(
        "SELECT id,first_arrival,due,state,chat,topic FROM piles ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if (
        latest
        and tuple(latest[4:]) == tuple(route)
        and latest[3] == "waiting"
        and arrival < datetime.fromisoformat(latest[2])
    ):
        pile, first = latest[0], datetime.fromisoformat(latest[1])
        due = min(first + window.ceiling, arrival + window.quiet)
        db.execute("UPDATE piles SET due=? WHERE id=?", (due.isoformat(), pile))
    else:
        cur = db.execute(
            "INSERT INTO piles(first_arrival,due,state,chat,topic) VALUES(?,?,'waiting',?,?)",
            (arrival.isoformat(), (arrival + window.quiet).isoformat(), *route),
        )
        pile = cur.lastrowid
    db.execute("INSERT INTO pile_members VALUES(?,?)", (update, pile))
