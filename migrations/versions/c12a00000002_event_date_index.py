"""Build a non-blocking index for the original event_date column."""
from alembic import op

revision = "c12a00000002"
down_revision = "c12a00000001"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
