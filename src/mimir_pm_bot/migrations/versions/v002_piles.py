from alembic import op

revision = "002_piles"
down_revision = "001_inbox"


def upgrade() -> None:
    op.execute("""CREATE TABLE piles (
        id INTEGER PRIMARY KEY, first_arrival TEXT NOT NULL, due TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('waiting','active','done','unknown')))""")
    op.execute("CREATE UNIQUE INDEX one_active_pile ON piles(state) WHERE state='active'")
    op.execute("""CREATE TABLE pile_members (
        update_id INTEGER PRIMARY KEY REFERENCES versions,
        pile INTEGER NOT NULL REFERENCES piles)""")


def downgrade() -> None:
    op.drop_table("pile_members")
    op.drop_table("piles")
