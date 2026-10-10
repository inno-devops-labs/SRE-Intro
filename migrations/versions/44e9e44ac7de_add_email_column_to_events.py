"""add email column to events

Revision ID: 44e9e44ac7de
Revises: e2d350f36fe7
Create Date: 2026-07-11 17:18:59.703753

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44e9e44ac7de'
down_revision: Union[str, Sequence[str], None] = 'e2d350f36fe7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
