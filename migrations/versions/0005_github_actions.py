"""Durable GitHub drafts, attempts and work projection."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None
metadata = sa.MetaData()
sa.Table("memory_turns", metadata, sa.Column("id", sa.Integer, primary_key=True))

action_versions = sa.Table(
    "action_versions",
    metadata,
    sa.Column("action_id", sa.String, primary_key=True),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("turn_id", sa.ForeignKey("memory_turns.id"), nullable=False),
    sa.Column("operation_key", sa.String, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("base_revision", sa.Integer, nullable=False),
    sa.Column("submitted", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("published", sa.Boolean, nullable=False, server_default="0"),
    sa.UniqueConstraint("turn_id", "operation_key"),
)
actions = sa.Table(
    "actions",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("retry_from", sa.Integer, nullable=False, server_default="0"),
    sa.Column("due", sa.Float, nullable=False, server_default="0"),
    sa.Column("detail", sa.String, nullable=False, server_default=""),
    sa.Column("result", sa.Text),
    sa.CheckConstraint(
        "status IN ('draft','ready','executing','succeeded','failed','cancelled','stale','unknown')"
    ),
)
sa.Index(
    "one_executing_action",
    actions.c.status,
    unique=True,
    sqlite_where=actions.c.status == "executing",
)
work_items = sa.Table(
    "work_items",
    metadata,
    sa.Column("issue_number", sa.Integer, primary_key=True),
    sa.Column("snapshot", sa.Text, nullable=False),
    sa.Column("observed_at", sa.Float, nullable=False),
    sa.Column("case_ids", sa.Text, nullable=False, server_default="[]"),
    sa.Column("metadata", sa.Text, nullable=False, server_default="{}"),
)


def upgrade() -> None:
    action_versions.create(op.get_bind())
    actions.create(op.get_bind())
    work_items.create(op.get_bind())
    action_attempts.create(op.get_bind())
    github_limits.create(op.get_bind())
    github_scope.create(op.get_bind())


def downgrade() -> None:
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(action_versions)):
        raise RuntimeError("Restore backup to retain GitHub action audit history")
    github_scope.drop(op.get_bind())
    github_limits.drop(op.get_bind())
    action_attempts.drop(op.get_bind())
    work_items.drop(op.get_bind())
    actions.drop(op.get_bind())
    action_versions.drop(op.get_bind())


action_attempts = sa.Table(
    "action_attempts",
    metadata,
    sa.Column("action_id", sa.ForeignKey("actions.id"), primary_key=True),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("attempt", sa.Integer, primary_key=True),
    sa.Column("started_at", sa.Float, nullable=False),
    sa.Column("finished_at", sa.Float),
    sa.Column("mode", sa.String, nullable=False),
    sa.Column("status", sa.String, nullable=False, server_default="executing"),
    sa.Column("detail", sa.String, nullable=False, server_default=""),
    sa.Column("result", sa.Text),
)

github_limits = sa.Table(
    "github_limits",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("not_before", sa.Float, nullable=False, server_default="0"),
    sa.Column("next_write", sa.Float, nullable=False, server_default="0"),
    sa.CheckConstraint("id=1"),
)

github_scope = sa.Table(
    "github_scope",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("repository", sa.String, nullable=False),
    sa.CheckConstraint("id=1"),
)
