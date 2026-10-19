"""Index events.event_date without blocking live writes."""

from alembic import op

revision = "c11800000001"
down_revision = "f5d36c02fa4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
