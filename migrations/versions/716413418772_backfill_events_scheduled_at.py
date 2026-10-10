"""backfill events.scheduled_at

Revision ID: 716413418772
Revises: 62e2f6d2502b
Create Date: 2026-07-17 21:49:59.876147

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '716413418772'
down_revision: Union[str, Sequence[str], None] = '62e2f6d2502b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill scheduled_at from event_date, then enforce NOT NULL.

    Idempotent (WHERE scheduled_at IS NULL) so a re-run is a no-op. Safe under
    live traffic because Deploy A reads via COALESCE and tolerates both NULL and
    non-NULL scheduled_at. On a 10M-row table this UPDATE would be batched
    (WHERE id BETWEEN x AND y, chunks of ~10k, sleeping between) to avoid a long
    transaction lock; QuickTicket's 5-row seed finishes instantly.
    """
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    # No need to UPDATE back — event_date still holds the data.
    op.alter_column("events", "scheduled_at", nullable=True)
