"""SQL tables owned by the storage adapter; migrations own schema creation."""

import sqlalchemy as sa

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
    sa.Column("reply_to_id", sa.BigInteger, nullable=True),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("due", sa.Float, nullable=False, server_default="0"),
    sa.Column("external_id", sa.BigInteger),
    sa.Column("delivery_key", sa.String, unique=True),
    sa.Column("target_id", sa.BigInteger),
    sa.Column("job_id", sa.Integer),
    sa.Column("depends_on", sa.Integer),
    sa.Column("operation", sa.String, nullable=False, server_default="message"),
    sa.Column("reaction_emoji", sa.String),
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

work_scan = sa.Table(
    "work_scan",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("snapshot", sa.Text, nullable=False),
    sa.CheckConstraint("id=1"),
)

work_schedule = sa.Table(
    "work_schedule",
    metadata,
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("local_day", sa.String, nullable=False),
    sa.Column("fingerprints", sa.Text, nullable=False),
)

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
