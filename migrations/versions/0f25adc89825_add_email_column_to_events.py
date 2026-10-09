"""add email column to events

Revision ID: 0f25adc89825
Revises: 598c51bd33f3
Create Date: 2026-10-09 02:39:07.654664

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f25adc89825'
down_revision: Union[str, Sequence[str], None] = '598c51bd33f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column(
        'events',
        sa.Column('email', sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('events', 'email')
