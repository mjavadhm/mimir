"""Source-backed memory and per-turn staged changes."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None
metadata = sa.MetaData()
# Dependencies are declared for foreign-key resolution only, not recreated.
sa.Table("jobs", metadata, sa.Column("id", sa.Integer, primary_key=True))

memory_turns = sa.Table(
    "memory_turns",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("token_hash", sa.String, unique=True, nullable=False),
    sa.Column("project_id", sa.String, nullable=False),
    sa.Column("job_id", sa.ForeignKey("jobs.id"), nullable=False),
    sa.Column("request_revision", sa.Integer, nullable=False),
    sa.Column("generation", sa.Integer, nullable=False),
    sa.Column("policy_version", sa.String, nullable=False),
    sa.Column("policy_text", sa.Text, nullable=False),
    sa.Column("state", sa.String, nullable=False, server_default="open"),
)
memory_stages = sa.Table(
    "memory_stages",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("turn_id", sa.ForeignKey("memory_turns.id"), nullable=False),
    sa.Column("operation_key", sa.String, nullable=False),
    sa.Column("input", sa.Text, nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("record_id", sa.String, nullable=False),
    sa.Column("expected_revision", sa.Integer, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.UniqueConstraint("turn_id", "operation_key"),
)
memory_revisions = sa.Table(
    "memory_revisions",
    metadata,
    sa.Column("record_id", sa.String, primary_key=True),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("turn_id", sa.ForeignKey("memory_turns.id"), nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("needs_review", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("created_at", sa.String, nullable=False),
)


def upgrade() -> None:
    memory_turns.create(op.get_bind())
    memory_stages.create(op.get_bind())
    memory_revisions.create(op.get_bind())


def downgrade() -> None:
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(memory_turns)):
        raise RuntimeError("Restore pre-upgrade backup to preserve memory audit history")
    memory_revisions.drop(op.get_bind())
    memory_stages.drop(op.get_bind())
    memory_turns.drop(op.get_bind())
