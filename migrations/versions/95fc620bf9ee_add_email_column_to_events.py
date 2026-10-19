"""add email column to events

Revision ID: 95fc620bf9ee
Revises: 0f88ce6b1ce4
Create Date: 2026-10-12 07:53:39.669492

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95fc620bf9ee'
down_revision: Union[str, Sequence[str], None] = '0f88ce6b1ce4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
