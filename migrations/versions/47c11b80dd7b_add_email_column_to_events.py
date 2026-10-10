"""add email column to events

Revision ID: 47c11b80dd7b
Revises: e3bbd9095744
Create Date: 2026-07-09 23:40:39.326867

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47c11b80dd7b'
down_revision: Union[str, Sequence[str], None] = 'e3bbd9095744'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))
    pass


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
    pass
