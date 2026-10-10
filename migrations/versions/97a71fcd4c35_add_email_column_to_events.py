"""add email column to events

Revision ID: 97a71fcd4c35
Revises: 48d652278c2e
Create Date: 2026-07-04 13:10:39.386807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97a71fcd4c35'
down_revision: Union[str, Sequence[str], None] = '48d652278c2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
