from alembic import op

revision = "008_attempts"
down_revision = "007_work"


def upgrade() -> None:
    op.execute("""CREATE TABLE issue_attempts(
        id INTEGER PRIMARY KEY, marker TEXT NOT NULL, draft TEXT NOT NULL,
        revision INTEGER NOT NULL, state TEXT NOT NULL, result TEXT, observed TEXT NOT NULL)""")
    op.execute("""CREATE TRIGGER issue_attempt_log AFTER UPDATE OF state ON issue_actions
        BEGIN INSERT INTO issue_attempts(marker,draft,revision,state,result,observed)
        VALUES(NEW.marker,NEW.draft,NEW.revision,NEW.state,NEW.result,CURRENT_TIMESTAMP); END""")


def downgrade() -> None:
    op.execute("DROP TRIGGER issue_attempt_log")
    op.drop_table("issue_attempts")
