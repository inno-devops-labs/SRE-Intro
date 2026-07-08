"""add email column to events

Revision ID: 638e632482d2
Revises: ec1d20aaee22
Create Date: 2026-07-08 21:40:34.261360

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '638e632482d2'
down_revision: Union[str, Sequence[str], None] = 'ec1d20aaee22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')