"""Persist the last work scan and daily notification state."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_scan",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("snapshot", sa.Text, nullable=False),
        sa.CheckConstraint("id=1"),
    )
    op.create_table(
        "work_schedule",
        sa.Column("name", sa.String, primary_key=True),
        sa.Column("local_day", sa.String, nullable=False),
        sa.Column("fingerprints", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("work_schedule")
    op.drop_table("work_scan")
