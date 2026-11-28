from alembic import op

revision = "007_work"
down_revision = "006_subjects"


def upgrade() -> None:
    op.execute("ALTER TABLE binding ADD COLUMN github_repo TEXT")
    op.execute("""CREATE TABLE issue_drafts(
        draft TEXT NOT NULL, revision INTEGER NOT NULL, decision TEXT NOT NULL,
        decision_revision INTEGER NOT NULL, subject INTEGER NOT NULL,
        title TEXT NOT NULL, body TEXT NOT NULL, pile INTEGER NOT NULL REFERENCES piles,
        PRIMARY KEY(draft,revision))""")
    op.execute("""CREATE TABLE issue_actions(
        draft TEXT PRIMARY KEY, revision INTEGER NOT NULL, marker TEXT NOT NULL UNIQUE,
        pile INTEGER NOT NULL REFERENCES piles, source INTEGER NOT NULL REFERENCES versions,
        state TEXT NOT NULL, due REAL NOT NULL DEFAULT 0, number INTEGER UNIQUE, result TEXT,
        FOREIGN KEY(draft,revision) REFERENCES issue_drafts)""")
    op.execute("""CREATE TABLE work_events(
        issue INTEGER NOT NULL REFERENCES issue_actions(number),
        pile INTEGER NOT NULL REFERENCES piles,
        source INTEGER NOT NULL REFERENCES versions, pull INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(issue,source,pull))""")
    op.execute("""CREATE TABLE work_observations(
        issue INTEGER PRIMARY KEY REFERENCES issue_actions(number), observed REAL NOT NULL,
        metadata TEXT NOT NULL)""")


def downgrade() -> None:
    for table in ("work_observations", "work_events", "issue_actions", "issue_drafts"):
        op.drop_table(table)
    op.drop_column("binding", "github_repo")
