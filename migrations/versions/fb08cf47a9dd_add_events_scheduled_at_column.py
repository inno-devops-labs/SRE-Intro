"""add events.scheduled_at column

Revision ID: fb08cf47a9dd
Revises: 66ae269dd593
Create Date: 2026-10-12 05:10:40.296702

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb08cf47a9dd'
down_revision: Union[str, Sequence[str], None] = '66ae269dd593'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Expand: new nullable column. Nullable + no default = metadata-only, instant
    # even on a big table; the old code never sees it.
    op.add_column(
        "events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    # Index it now, concurrently: migration 3 drops event_date and with it
    # idx_events_event_date (Task 2), so the replacement must exist before then.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at",
            "events",
            ["scheduled_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_scheduled_at",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("events", "scheduled_at")
