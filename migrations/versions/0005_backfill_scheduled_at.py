"""Backfill the six-row lab fixture before enforcing the new constraint."""

from alembic import op


revision = "0005_backfill_scheduled_at"
down_revision = "0004_add_scheduled_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    op.alter_column("events", "scheduled_at", nullable=True)
