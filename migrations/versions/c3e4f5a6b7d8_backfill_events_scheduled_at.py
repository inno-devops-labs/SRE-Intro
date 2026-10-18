"""backfill events.scheduled_at

Revision ID: c3e4f5a6b7d8
Revises: b2d3e4f5a6c7
Create Date: 2026-10-19 00:20:00.000000
"""
from alembic import op

revision = "c3e4f5a6b7d8"
down_revision = "b2d3e4f5a6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade():
    op.alter_column("events", "scheduled_at", nullable=True)
