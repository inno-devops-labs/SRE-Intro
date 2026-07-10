"""add email column to events

Revision ID: a8a72f006699
Revises: 3256eabd4c59
Create Date: 2026-07-08 19:04:54.240378

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8a72f006699'
down_revision: Union[str, Sequence[str], None] = '3256eabd4c59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
