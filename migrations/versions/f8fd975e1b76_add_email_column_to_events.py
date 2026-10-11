"""add email column to events

Revision ID: f8fd975e1b76
Revises: 83c8377a61dc
Create Date: 2026-10-11 18:22:23.540963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8fd975e1b76'
down_revision: Union[str, Sequence[str], None] = '83c8377a61dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
