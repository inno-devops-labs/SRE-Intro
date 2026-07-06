"""backfill events.scheduled_at

Revision ID: 8eb3a80d6ad5
Revises: c12c4ca8bc96
Create Date: 2026-07-06 23:14:37.154353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8eb3a80d6ad5'
down_revision: Union[str, Sequence[str], None] = 'c12c4ca8bc96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('events', 'scheduled_at', nullable=True)
