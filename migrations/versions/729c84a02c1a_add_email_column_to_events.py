"""add email column to events

Revision ID: 729c84a02c1a
Revises: 6937f4406734
Create Date: 2026-07-09 13:24:17.507014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '729c84a02c1a'
down_revision: Union[str, Sequence[str], None] = '6937f4406734'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'email')
