"""Backfill events.scheduled_at and enforce NOT NULL."""

from alembic import op

revision = "a12c03"
down_revision = "a12c02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    op.alter_column("events", "scheduled_at", nullable=True)
