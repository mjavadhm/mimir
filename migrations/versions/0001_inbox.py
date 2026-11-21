"""Initial inbox, message versions, checkpoint, and health outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_inbox"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "checkpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_offset", sa.Integer(), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "checkpoint",
            sa.column("id", sa.Integer),
            sa.column("next_offset", sa.Integer),
        ),
        [{"id": 1, "next_offset": 0}],
    )
    op.create_table(
        "inbox",
        sa.Column("update_id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("admission", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("received_at", sa.String(), nullable=False),
    )
    op.create_table(
        "message_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("update_id", sa.Integer(), sa.ForeignKey("inbox.update_id"), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("reply_to_message_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("message_at", sa.String(), nullable=False),
        sa.Column("edited_at", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("update_id"),
        sa.UniqueConstraint("chat_id", "message_id", "version"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("update_id", sa.Integer(), sa.ForeignKey("inbox.update_id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("external_message_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("update_id"),
    )


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("message_versions")
    op.drop_table("inbox")
    op.drop_table("checkpoint")
