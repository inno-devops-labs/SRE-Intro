"""Backfill scheduled_at, then require a date.

Revision ID: 12a000000003
Revises: 12a000000002
"""
from alembic import op
import sqlalchemy as sa

revision = "12a000000003"
down_revision = "12a000000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=True,
    )
