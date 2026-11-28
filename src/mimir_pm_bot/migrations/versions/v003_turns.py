from alembic import op

revision = "003_turns"
down_revision = "002_piles"


def upgrade() -> None:
    op.execute("ALTER TABLE piles ADD COLUMN detail TEXT")
    op.execute("""CREATE TABLE outputs (
        id INTEGER PRIMARY KEY, pile INTEGER NOT NULL REFERENCES piles,
        call_key TEXT NOT NULL, part INTEGER NOT NULL, kind TEXT NOT NULL,
        payload TEXT NOT NULL, fingerprint TEXT NOT NULL, target_version INTEGER,
        state TEXT NOT NULL DEFAULT 'pending', due REAL NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0, result TEXT,
        UNIQUE(pile,call_key,part))""")
    op.execute("""CREATE TABLE admin_commands (
        update_id INTEGER PRIMARY KEY REFERENCES inbox, actor INTEGER NOT NULL,
        command TEXT NOT NULL, target_pile INTEGER, claimed INTEGER DEFAULT 0)""")


def downgrade() -> None:
    op.drop_table("admin_commands")
    op.drop_table("outputs")
    op.drop_column("piles", "detail")
