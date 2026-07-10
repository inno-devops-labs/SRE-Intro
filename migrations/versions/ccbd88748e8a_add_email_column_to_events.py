"""add email column to events

Revision ID: ccbd88748e8a
Revises: c776243946eb
Create Date: 2026-07-10 02:50:41.078640

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ccbd88748e8a'
down_revision: Union[str, Sequence[str], None] = 'c776243946eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')