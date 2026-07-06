"""add events.scheduled_at column

Revision ID: c12c4ca8bc96
Revises: 909d8c12c729
Create Date: 2026-07-06 23:11:44.018919

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c12c4ca8bc96'
down_revision: Union[str, Sequence[str], None] = '909d8c12c729'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'events',
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'scheduled_at')
