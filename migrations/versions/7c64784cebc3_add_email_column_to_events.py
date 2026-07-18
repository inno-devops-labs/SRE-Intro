"""add email column to events

Revision ID: 7c64784cebc3
Revises: ea53f4ac63fc
Create Date: 2026-07-11 07:12:39.794738

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c64784cebc3'
down_revision: Union[str, Sequence[str], None] = 'ea53f4ac63fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
