"""add email column to events

Revision ID: 5a0d1c908362
Revises: 46c09a1a9cfd
Create Date: 2026-07-07 22:42:42.022398

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a0d1c908362'
down_revision: Union[str, Sequence[str], None] = '46c09a1a9cfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
