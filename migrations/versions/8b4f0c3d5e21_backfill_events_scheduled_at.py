"""backfill events.scheduled_at

Revision ID: 8b4f0c3d5e21
Revises: 7a3e9b2c4d10
Create Date: 2026-10-18 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b4f0c3d5e21'
down_revision: Union[str, Sequence[str], None] = '7a3e9b2c4d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Safe under traffic: Deploy A reads COALESCE(scheduled_at, event_date), so
    # a row reads the same before and after it is backfilled. The WHERE makes a
    # re-run a no-op. At production size this would run in batches (see
    # submissions/lab12.md), not as one UPDATE.
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
    # Dropping event_date in the next migration also drops idx_events_event_date
    # (12.7), so the replacement index is built here, before anything reads by
    # scheduled_at alone.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at", "events", ["scheduled_at"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_scheduled_at", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
    # event_date still holds the data, nothing to copy back.
    op.alter_column("events", "scheduled_at", nullable=True)
