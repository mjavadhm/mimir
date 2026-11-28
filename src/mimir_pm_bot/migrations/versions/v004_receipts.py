from alembic import op

revision = "004_receipts"
down_revision = "003_turns"


def upgrade() -> None:
    op.execute("ALTER TABLE outputs ADD COLUMN request_hash TEXT")
    op.execute("ALTER TABLE binding ADD COLUMN pending_model TEXT")


def downgrade() -> None:
    op.drop_column("binding", "pending_model")
    op.drop_column("outputs", "request_hash")
