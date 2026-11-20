"""Durable batches, agent state and semantic outbox keys."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
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
    sa.Column("source_update_id", sa.ForeignKey("inbox.update_id"), nullable=True),
    sa.Column("chat_id", sa.BigInteger, nullable=False),
    sa.Column("topic_id", sa.Integer, nullable=False),
    sa.Column("reply_to_id", sa.BigInteger, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("due", sa.Float, nullable=False, server_default="0"),
    sa.Column("external_id", sa.BigInteger),
    sa.Column("delivery_key", sa.String, unique=True),
    sa.Column("target_id", sa.BigInteger),
    sa.Column("job_id", sa.Integer),
    sa.Column("depends_on", sa.Integer),
    sa.Column("purpose", sa.String, nullable=False, server_default="reply"),
    sa.CheckConstraint("status IN ('pending','sending','sent','failed','unknown')"),
    sa.Index("ix_outbox_due", "status", "due"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("first_at", sa.Float, nullable=False),
    sa.Column("due", sa.Float, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("generation", sa.Integer, nullable=False, server_default="0"),
    sa.Column("result", sa.Text),
    sa.CheckConstraint(
        "status IN ('pending','running','succeeded','cancelled','superseded','failed','unknown')"
    ),
)
sa.Index("one_running_job", jobs.c.status, unique=True, sqlite_where=jobs.c.status == "running")
job_messages = sa.Table(
    "job_messages",
    metadata,
    sa.Column("job_id", sa.ForeignKey("jobs.id"), primary_key=True),
    sa.Column("message_id", sa.BigInteger, primary_key=True),
    sa.Column("update_id", sa.ForeignKey("inbox.update_id"), nullable=False),
)
agent_state = sa.Table(
    "agent_state",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("session_id", sa.String),
    sa.Column("generation", sa.Integer, nullable=False, server_default="0"),
    sa.Column("blocked", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("turbo_requested", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("turbo_effective", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("card_id", sa.BigInteger),
    sa.Column("card_text", sa.String, nullable=False, server_default=""),
    sa.Column("card_at", sa.Float, nullable=False, server_default="0"),
)


def upgrade() -> None:
    op.rename_table("outbox", "outbox_v1")
    op.drop_index("ix_outbox_due", table_name="outbox_v1")
    outbox.create(op.get_bind())
    op.execute(
        "INSERT INTO outbox (id,source_update_id,chat_id,topic_id,reply_to_id,text,status,"
        "attempts,due,external_id) SELECT id,source_update_id,chat_id,topic_id,reply_to_id,"
        "text,status,attempts,due,external_id FROM outbox_v1"
    )
    op.drop_table("outbox_v1")
    jobs.create(op.get_bind())
    job_messages.create(op.get_bind())
    agent_state.create(op.get_bind())
    op.execute("INSERT INTO agent_state (id) VALUES (1)")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.select(sa.func.count()).select_from(jobs)):
        raise RuntimeError(
            "Restore pre-upgrade backup; queue history cannot be downgraded losslessly"
        )
    agent_state.drop(connection)
    job_messages.drop(connection)
    jobs.drop(connection)
    with op.batch_alter_table(
        "outbox",
        table_args=(
            sa.CheckConstraint(
                "status IN ('pending','sending','sent','failed','unknown')", name="outbox_status"
            ),
        ),
    ) as batch:
        batch.create_unique_constraint("uq_outbox_source", ["source_update_id"])
        batch.drop_column("delivery_key")
        batch.drop_column("target_id")
        batch.drop_column("job_id")
        batch.drop_column("depends_on")
        batch.drop_column("purpose")
