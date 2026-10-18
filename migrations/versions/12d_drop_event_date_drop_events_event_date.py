"""drop events event date."""
from alembic import op
import sqlalchemy as sa

revision = "12d_drop_event_date"
down_revision = "12c_backfill_scheduled_at"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.drop_column("events", "event_date")


def downgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.add_column("events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"],
                        postgresql_concurrently=True, if_not_exists=True)
