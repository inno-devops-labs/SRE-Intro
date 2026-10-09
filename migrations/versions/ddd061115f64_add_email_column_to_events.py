"""add email column to events

Revision ID: ddd061115f64
Revises: 0bd68c000e8a
Create Date: 2026-10-09 21:53:43.176185

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ddd061115f64'
down_revision: Union[str, Sequence[str], None] = '0bd68c000e8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
