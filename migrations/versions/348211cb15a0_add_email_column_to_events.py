"""add email column to events

Revision ID: 348211cb15a0
Revises: 4c99d2b4cc90
Create Date: 2026-07-05 20:46:49.570521

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '348211cb15a0'
down_revision: Union[str, Sequence[str], None] = '4c99d2b4cc90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+.
    # It avoids a table rewrite and is safe to run while traffic is active.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
