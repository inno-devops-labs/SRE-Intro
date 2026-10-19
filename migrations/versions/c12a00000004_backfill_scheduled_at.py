"""Populate scheduled_at; only enforce NOT NULL once reads tolerate both."""
from alembic import op

revision = "c12a00000004"
down_revision = "c12a00000003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade():
    op.alter_column("events", "scheduled_at", nullable=True)
