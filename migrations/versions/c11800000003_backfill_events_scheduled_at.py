"""Copy existing dates before requiring scheduled_at."""

from alembic import op

revision = "c11800000003"
down_revision = "c11800000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    op.alter_column("events", "scheduled_at", nullable=True)
