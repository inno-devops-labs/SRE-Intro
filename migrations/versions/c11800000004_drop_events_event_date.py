"""Contract events after all application pods use scheduled_at."""

import sqlalchemy as sa
from alembic import op

revision = "c11800000004"
down_revision = "c11800000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column("events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            postgresql_concurrently=True, if_not_exists=True,
        )
