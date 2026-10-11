"""add email column to events

Revision ID: 43b43772c5f3
Revises: 77bf50754a91
Create Date: 2026-10-11 19:03:54.258280

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '43b43772c5f3'
down_revision: Union[str, Sequence[str], None] = '77bf50754a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default: PostgreSQL 11+ only updates the catalog, no table
    # rewrite. It still needs a brief ACCESS EXCLUSIVE lock on events, so the
    # migration must not wait behind a long transaction (see lock_timeout in env.py).
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
