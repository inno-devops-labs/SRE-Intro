"""backfill events scheduled at."""
from alembic import op
import sqlalchemy as sa

revision = "12c_backfill_scheduled_at"
down_revision = "12b_add_scheduled_at"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade():
    op.alter_column("events", "scheduled_at", nullable=True)
