from alembic import op

revision = "012_destination"
down_revision = "011_reviews"


def upgrade() -> None:
    op.execute("ALTER TABLE binding ADD COLUMN destination_managed INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE TABLE destination_setup(actor INTEGER PRIMARY KEY, proposed_chat INTEGER)")
    op.execute(
        "CREATE TABLE destination_probes(update_id INTEGER PRIMARY KEY, state TEXT NOT NULL)"
    )
    for field in ("chat", "topic"):
        op.execute(f"ALTER TABLE piles ADD COLUMN {field} INTEGER")
        op.execute(f"UPDATE piles SET {field}=(SELECT {field} FROM binding)")


def downgrade() -> None:
    op.drop_column("piles", "topic")
    op.drop_column("piles", "chat")
    op.drop_table("destination_probes")
    op.drop_table("destination_setup")
    op.drop_column("binding", "destination_managed")
