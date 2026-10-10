"""Expand events with the nullable scheduled_at column.

Revision ID: 12a000000002
Revises: 12a000000001
"""
from alembic import op
import sqlalchemy as sa

revision = "12a000000002"
down_revision = "12a000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("events", "scheduled_at")
