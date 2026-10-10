"""backfill events.scheduled_at

Revision ID: dac309c7e663
Revises: fcbd2eb78d60
Create Date: 2026-07-09 20:51:37.854728

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dac309c7e663'
down_revision: Union[str, Sequence[str], None] = 'fcbd2eb78d60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('events', 'scheduled_at', nullable=True)
    # event_date still holds the data, so no reverse UPDATE is needed.
