"""add index on events name concurrently

Revision ID: bc504a88c318
Revises: 44e9e44ac7de
Create Date: 2026-07-17 22:21:53.311205

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bc504a88c318'
down_revision: Union[str, Sequence[str], None] = '44e9e44ac7de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.create_index("ix_events_name", "events", ["name"], unique=False, postgresql_concurrently=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_events_name", table_name="events")
