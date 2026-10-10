"""Drop the contracted events.event_date column."""

from alembic import op
import sqlalchemy as sa

revision = "a12c04"
down_revision = "a12c03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column(
        "events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
