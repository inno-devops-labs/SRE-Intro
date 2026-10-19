"""add email column to events

Revision ID: f5d36c02fa4b
Revises: 71d59833c48b
Create Date: 2026-10-12 04:49:36.026900

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5d36c02fa4b'
down_revision: Union[str, Sequence[str], None] = '71d59833c48b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
