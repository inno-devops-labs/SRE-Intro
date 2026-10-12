"""add email column to events

Revision ID: ccbd88748e8a
Revises: c776243946eb
Create Date: 2026-10-11 14:20:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'ccbd88748e8a'
down_revision: Union[str, None] = 'c776243946eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(length=255), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'email')