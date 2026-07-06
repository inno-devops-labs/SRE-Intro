"""add email column to events

Revision ID: 640b23da79b1
Revises: 2c25160eb3c5
Create Date: 2026-07-06 14:37:32.834161

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '640b23da79b1'
down_revision: Union[str, Sequence[str], None] = '2c25160eb3c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')