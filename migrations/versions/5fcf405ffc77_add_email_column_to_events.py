"""add email column to events

Revision ID: 5fcf405ffc77
Revises: f654912bc7e1
Create Date: 2026-10-11 16:28:44.609418

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5fcf405ffc77'
down_revision: Union[str, Sequence[str], None] = 'f654912bc7e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite. It still needs a brief ACCESS EXCLUSIVE lock on `events`;
    # lock_timeout makes the migration fail fast instead of queueing behind a
    # long transaction and blocking every reader queued behind it.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
