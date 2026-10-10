"""add email column to events

Revision ID: 6803cfdeb137
Revises: debe1e371cd3
Create Date: 2026-07-06 17:45:02.150922

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6803cfdeb137'
down_revision: Union[str, Sequence[str], None] = 'debe1e371cd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable column add is metadata-only in modern PostgreSQL and is safe under load.
    op.add_column('events', sa.Column('email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'email')
