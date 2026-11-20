"""Initial durable Telegram inbox, message evidence and outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()
inbox = sa.Table(
    "inbox",
    metadata,
    sa.Column("update_id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("payload", sa.Text),
    sa.Column("status", sa.String, nullable=False),
    sa.CheckConstraint("status IN ('pending','processed','ignored')"),
)
checkpoint = sa.Table(
    "checkpoint",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("offset", sa.Integer, nullable=False),
    sa.Column("project_id", sa.String, nullable=False),
    sa.CheckConstraint("id = 1"),
)
versions = sa.Table(
    "message_versions",
    metadata,
    sa.Column("update_id", sa.ForeignKey("inbox.update_id"), primary_key=True),
    sa.Column("chat_id", sa.BigInteger, nullable=False),
    sa.Column("message_id", sa.BigInteger, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Index("ix_message_identity", "chat_id", "message_id", "update_id"),
)
outbox = sa.Table(
    "outbox",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("source_update_id", sa.ForeignKey("inbox.update_id"), nullable=False, unique=True),
    sa.Column("chat_id", sa.BigInteger, nullable=False),
    sa.Column("topic_id", sa.Integer, nullable=False),
    sa.Column("reply_to_id", sa.BigInteger, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("due", sa.Float, nullable=False, server_default="0"),
    sa.Column("external_id", sa.BigInteger),
    sa.CheckConstraint("status IN ('pending','sending','sent','failed','unknown')"),
    sa.Index("ix_outbox_due", "status", "due"),
)


def upgrade() -> None:
    metadata.create_all(op.get_bind())


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
