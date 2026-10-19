"""Index the legacy event date without blocking ordinary writes."""

from alembic import op


revision = "0003_event_date_index"
down_revision = "0002_events_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"], postgresql_concurrently=True, if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("idx_events_event_date", table_name="events", postgresql_concurrently=True, if_exists=True)
