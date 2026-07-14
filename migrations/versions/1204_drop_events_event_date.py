"""Contract events by removing the legacy event_date column."""

import sqlalchemy as sa
from alembic import op

revision = "1204"
down_revision = "1203"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("events", "event_date")


def downgrade():
    op.add_column(
        "events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
