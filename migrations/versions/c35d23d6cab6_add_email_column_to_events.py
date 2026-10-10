"""add email column to events

Revision ID: c35d23d6cab6
Revises: 5784f05e56f7
Create Date: 2026-07-10 21:46:05.459729

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c35d23d6cab6'
down_revision: Union[str, Sequence[str], None] = '5784f05e56f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
