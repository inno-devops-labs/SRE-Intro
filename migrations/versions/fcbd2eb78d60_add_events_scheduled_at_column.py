"""add events.scheduled_at column

Revision ID: fcbd2eb78d60
Revises: 3566ff6cdbcf
Create Date: 2026-07-09 20:51:18.866852

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fcbd2eb78d60'
down_revision: Union[str, Sequence[str], None] = '3566ff6cdbcf'
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
