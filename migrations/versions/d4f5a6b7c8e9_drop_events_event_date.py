"""drop events.event_date

Revision ID: d4f5a6b7c8e9
Revises: c3e4f5a6b7d8
Create Date: 2026-10-19 00:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "d4f5a6b7c8e9"
down_revision = "c3e4f5a6b7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("events", "event_date")


def downgrade():
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
