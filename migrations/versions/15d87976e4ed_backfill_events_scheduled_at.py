"""backfill events.scheduled_at

Revision ID: 15d87976e4ed
Revises: ffbc3dfe96aa
Create Date: 2026-07-18 06:01:34.287833

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15d87976e4ed'
down_revision: Union[str, Sequence[str], None] = 'ffbc3dfe96aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("events", "scheduled_at", nullable=True)
    # No need to UPDATE back — event_date still has the data.
