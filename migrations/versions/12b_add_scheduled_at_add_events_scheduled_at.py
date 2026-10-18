"""add events scheduled at."""
from alembic import op
import sqlalchemy as sa

revision = "12b_add_scheduled_at"
down_revision = "12a_index_event_date"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade():
    op.drop_column("events", "scheduled_at")
