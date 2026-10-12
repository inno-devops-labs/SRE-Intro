"""backfill events.scheduled_at

Revision ID: 41d677444ecf
Revises: fb08cf47a9dd
Create Date: 2026-10-12 05:10:55.497092

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41d677444ecf'
down_revision: Union[str, Sequence[str], None] = 'fb08cf47a9dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Safe under live traffic: Deploy A reads COALESCE(scheduled_at, event_date),
    # so rows are correct before and after this UPDATE, and WHERE ... IS NULL makes
    # it idempotent. On a big table this would be batched (see submission).
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    # event_date still holds the data, nothing to copy back.
    op.alter_column("events", "scheduled_at", nullable=True)
