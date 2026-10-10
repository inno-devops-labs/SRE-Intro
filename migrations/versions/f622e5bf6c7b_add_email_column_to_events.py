"""add email column to events

Revision ID: f622e5bf6c7b
Revises: b27d36103632
Create Date: 2026-07-11 01:26:40.782780

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f622e5bf6c7b'
down_revision: Union[str, Sequence[str], None] = 'b27d36103632'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
