"""add email column to events

Revision ID: 2346b9fa9878
Revises: fe19828ebd08
Create Date: 2026-07-10 21:25:39.119141

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2346b9fa9878'
down_revision: Union[str, Sequence[str], None] = 'fe19828ebd08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'email')
