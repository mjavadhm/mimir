from alembic import op

revision = "010_incidents"
down_revision = "009_turn_cards"

# Only application-owned descriptors go into channel messages.
WATCHED = (
    (
        "piles",
        "Antigravity",
        "id",
        "NEW.state='unknown' OR (NEW.state='done' AND NEW.detail NOT IN ('end_turn','cancelled'))",
    ),
    ("outputs", "Telegram delivery", "pile", "NEW.state IN ('unknown','failed')"),
    ("turn_cards", "Progress card", "pile", "NEW.state IN ('unknown','failed')"),
    ("issue_actions", "GitHub issue", "pile", "NEW.state IN ('unknown','failed')"),
)


def upgrade() -> None:
    op.execute("""CREATE TABLE incidents(
        id INTEGER PRIMARY KEY, component TEXT NOT NULL, reason TEXT NOT NULL,
        pile INTEGER, occurred TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        state TEXT NOT NULL DEFAULT 'pending', due REAL NOT NULL DEFAULT 0)""")
    for table, label, ref, condition in WATCHED:
        op.execute(f"""CREATE TRIGGER incident_{table} AFTER UPDATE OF state ON {table}
            WHEN OLD.state!=NEW.state AND ({condition})
            BEGIN INSERT INTO incidents(component,reason,pile)
            VALUES('{label}',CASE WHEN NEW.state='done' THEN 'abnormal stop'
            ELSE NEW.state END,NEW.{ref}); END""")


def downgrade() -> None:
    for table, *_ in WATCHED:
        op.execute(f"DROP TRIGGER incident_{table}")
    op.drop_table("incidents")
