"""Backfill scheduled_at and make it required."""

from alembic import op

revision = "1203"
down_revision = "1202"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade():
    op.alter_column("events", "scheduled_at", nullable=True)
