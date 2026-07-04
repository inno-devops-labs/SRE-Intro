"""backfill events.scheduled_at

Revision ID: b2b755d1be72
Revises: 8d336004eb32
Create Date: 2026-07-04 19:58:58.113446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2b755d1be72'
down_revision: Union[str, Sequence[str], None] = '8d336004eb32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Backfill: idempotent (WHERE scheduled_at IS NULL) so re-running is a no-op.
    # Safe under live traffic because Deploy A reads via COALESCE(scheduled_at,
    # event_date) and tolerates both NULL and non-NULL scheduled_at.
    # On a 10M-row table you would batch this (UPDATE ... WHERE id BETWEEN x AND
    # y in ~10k chunks with a sleep between) to keep each transaction short.
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    # Only AFTER the backfill can we enforce NOT NULL.
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    # No need to UPDATE back — event_date still holds the data.
    op.alter_column("events", "scheduled_at", nullable=True)
