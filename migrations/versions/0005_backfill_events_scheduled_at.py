"""Backfill the tiny lab table; allow new-column-only writers."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
    op.alter_column("events", "event_date", nullable=True)


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
    op.alter_column("events", "scheduled_at", nullable=True)
