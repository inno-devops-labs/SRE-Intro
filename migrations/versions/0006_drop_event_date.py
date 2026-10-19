"""Contract only after every events pod uses scheduled_at exclusively."""

from alembic import op
import sqlalchemy as sa


revision = "0006_drop_event_date"
down_revision = "0005_backfill_scheduled_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index("idx_events_scheduled_at", "events", ["scheduled_at"], postgresql_concurrently=True, if_not_exists=True)
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column("events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"], postgresql_concurrently=True, if_not_exists=True)
        op.drop_index("idx_events_scheduled_at", table_name="events", postgresql_concurrently=True, if_exists=True)
