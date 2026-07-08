"""add email column to events

Revision ID: 56780f89edf0
Revises: 538b3ff59ef9
Create Date: 2026-07-08 23:39:41.316360

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56780f89edf0'
down_revision: Union[str, Sequence[str], None] = '538b3ff59ef9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Adding a NULLABLE column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite and no blocking lock on SELECT/INSERT, so it is safe to
    # run under live traffic. (A NOT NULL column without a default would rewrite
    # the whole table under an exclusive lock — a classic outage cause.)
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
