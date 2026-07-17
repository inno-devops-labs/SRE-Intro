"""add email column to events

Revision ID: 4cb7a9e36b09
Revises: e00e25ace275
Create Date: 2026-07-04 19:07:42.882091

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4cb7a9e36b09'
down_revision: Union[str, Sequence[str], None] = 'e00e25ace275'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
