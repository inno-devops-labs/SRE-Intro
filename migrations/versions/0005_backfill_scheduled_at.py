"""Backfill after Deploy A, before Deploy B (tiny course dataset)."""
from alembic import op
revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None

def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)

def downgrade():
    op.alter_column("events", "scheduled_at", nullable=True)
