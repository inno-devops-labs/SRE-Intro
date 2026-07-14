"""Expand events with the nullable scheduled_at column."""

import sqlalchemy as sa
from alembic import op

revision = "1202"
down_revision = "1201"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        if_not_exists=True,
    )


def downgrade():
    op.drop_column("events", "scheduled_at")
