"""Expand events with a nullable scheduled_at column."""

import sqlalchemy as sa
from alembic import op

revision = "c11800000002"
down_revision = "c11800000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
