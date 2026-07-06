"""drop events.event_date

Revision ID: dc801d570f83
Revises: 8eb3a80d6ad5
Create Date: 2026-07-06 23:20:09.722715

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dc801d570f83'
down_revision: Union[str, Sequence[str], None] = '8eb3a80d6ad5'
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
