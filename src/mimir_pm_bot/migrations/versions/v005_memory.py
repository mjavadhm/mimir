from alembic import op

revision = "005_memory"
down_revision = "004_receipts"


def upgrade() -> None:
    op.execute("""CREATE TABLE subjects (
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, title_key TEXT NOT NULL UNIQUE)""")
    op.execute("""CREATE TABLE decision_versions (
        id INTEGER PRIMARY KEY, decision TEXT NOT NULL, revision INTEGER NOT NULL,
        subject INTEGER NOT NULL REFERENCES subjects, body TEXT NOT NULL, reason TEXT,
        status TEXT NOT NULL CHECK(status IN ('active','inactive')),
        pile INTEGER NOT NULL REFERENCES piles, operation TEXT NOT NULL, request TEXT NOT NULL,
        committed_at TEXT, UNIQUE(decision,revision), UNIQUE(pile,operation))""")
    op.execute("""CREATE TABLE decision_sources (
        entry INTEGER NOT NULL REFERENCES decision_versions ON DELETE CASCADE,
        source INTEGER NOT NULL REFERENCES versions(update_id), PRIMARY KEY(entry,source))""")
    op.execute("""CREATE TABLE memory_reads (
        pile INTEGER NOT NULL REFERENCES piles, tool TEXT NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY(pile,tool))""")


def downgrade() -> None:
    for table in ("memory_reads", "decision_sources", "decision_versions", "subjects"):
        op.drop_table(table)
