"""Optional reply targets and durable reaction intents."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table(
        "outbox",
        table_args=(
            sa.CheckConstraint(
                "status IN ('pending','sending','sent','failed','unknown')", name="outbox_status"
            ),
        ),
    ) as batch:
        batch.alter_column("reply_to_id", existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(
            sa.Column("operation", sa.String(), nullable=False, server_default="message")
        )
        batch.add_column(sa.Column("reaction_emoji", sa.String(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    count = connection.scalar(
        sa.text("SELECT count(*) FROM outbox WHERE operation != 'message' OR reply_to_id IS NULL")
    )
    if count:
        raise RuntimeError("Restore backup: action history cannot be downgraded losslessly")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_column("reaction_emoji")
        batch.drop_column("operation")
        batch.alter_column("reply_to_id", existing_type=sa.BigInteger(), nullable=False)
