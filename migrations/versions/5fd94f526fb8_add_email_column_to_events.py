"""add email column to events

Revision ID: 5fd94f526fb8
Revises: 66354c5537fc
Create Date: 2026-09-22 16:29:00.863426

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5fd94f526fb8'
down_revision: Union[str, Sequence[str], None] = '66354c5537fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a nullable `email` column to `events`.

    Adding a NULLABLE column with no default is a catalog-only change in
    PostgreSQL 11+: no table rewrite, and the ACCESS EXCLUSIVE lock is held only
    for the catalog update (milliseconds). Concurrent SELECT/INSERT on `events`
    are not blocked in any way that a client would notice, so this is safe to
    run under live traffic.

    The dangerous variant — and a well-known outage cause — is adding a NOT NULL
    column WITHOUT a default: that fails outright, and the historical
    NOT NULL + volatile default form rewrites every row while holding
    ACCESS EXCLUSIVE, blocking all readers and writers for the duration.
    """
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Drop the `email` column again (also catalog-only)."""
    op.drop_column('events', 'email')
