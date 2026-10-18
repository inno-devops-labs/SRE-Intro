"""add events.scheduled_at column

Revision ID: 64f12464912c
Revises: 8e028e241bb0
Create Date: 2026-10-18 15:09:44.943218

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64f12464912c'
down_revision: Union[str, Sequence[str], None] = '8e028e241bb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'scheduled_at')
