from alembic import op

revision = "006_subjects"
down_revision = "005_memory"


def upgrade() -> None:
    op.execute("""CREATE TABLE subject_openings (
        subject INTEGER PRIMARY KEY REFERENCES subjects, pile INTEGER NOT NULL REFERENCES piles,
        operation TEXT NOT NULL, request TEXT NOT NULL, committed_at TEXT,
        UNIQUE(pile,operation))""")


def downgrade() -> None:
    op.drop_table("subject_openings")
