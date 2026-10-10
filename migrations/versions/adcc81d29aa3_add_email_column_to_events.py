"""add email column to events

Revision ID: adcc81d29aa3
Revises: 7488189f281f
Create Date: 2026-07-08 19:03:44.039571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'adcc81d29aa3'
down_revision: Union[str, Sequence[str], None] = '7488189f281f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
