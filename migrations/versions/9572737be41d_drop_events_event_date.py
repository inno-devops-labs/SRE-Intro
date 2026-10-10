"""drop events.event_date

Revision ID: 9572737be41d
Revises: dac309c7e663
Create Date: 2026-07-09 20:51:37.990891

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9572737be41d'
down_revision: Union[str, Sequence[str], None] = 'dac309c7e663'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('events', 'event_date')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'events',
        sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column('events', 'event_date', nullable=False)
