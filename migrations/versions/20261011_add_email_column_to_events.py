"""add email column to events

Revision ID: 7ae0d8c4d5f2
Revises: 30d1980bee5c
Create Date: 2026-10-11 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "7ae0d8c4d5f2"
down_revision: Union[str, Sequence[str], None] = "30d1980bee5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
