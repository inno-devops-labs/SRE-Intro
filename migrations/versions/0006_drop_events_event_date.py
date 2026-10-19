"""Contract only after every Events pod uses scheduled_at."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    # Keep the date index when its old column is removed.
    with op.get_context().autocommit_block():
        op.create_index("idx_events_scheduled_at", "events", ["scheduled_at"],
                        postgresql_concurrently=True, if_not_exists=True)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("events", "event_date")


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    # Matches 0005: old column remains nullable for Deploy B writers.
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"],
                        postgresql_concurrently=True, if_not_exists=True)
        op.drop_index("idx_events_scheduled_at", table_name="events",
                      postgresql_concurrently=True, if_exists=True)
