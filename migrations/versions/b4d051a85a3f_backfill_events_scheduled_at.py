"""backfill events.scheduled_at

Revision ID: b4d051a85a3f
Revises: 09ebb6ce241b
Create Date: 2026-10-06 11:53:33.717068

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d051a85a3f'
down_revision: Union[str, Sequence[str], None] = '09ebb6ce241b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill is idempotent (WHERE scheduled_at IS NULL) — safe to re-run,
    # and safe under live traffic because Deploy A reads via COALESCE and
    # tolerates both NULL and non-NULL scheduled_at.
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    # Only after every row is populated can the column become NOT NULL.
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade() -> None:
    # No UPDATE back needed — event_date still holds the data.
    op.alter_column('events', 'scheduled_at', nullable=True)
