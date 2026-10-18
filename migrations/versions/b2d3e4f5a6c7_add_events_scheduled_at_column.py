"""add events.scheduled_at column

Revision ID: b2d3e4f5a6c7
Revises: a1c2d3e4f5b6
Create Date: 2026-10-19 00:10:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d3e4f5a6c7"
down_revision = "a1c2d3e4f5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("events", "scheduled_at")
