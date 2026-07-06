"""index events.event_date concurrently

Revision ID: 909d8c12c729
Revises: 640b23da79b1
Create Date: 2026-07-06 22:58:39.295273

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '909d8c12c729'
down_revision: Union[str, Sequence[str], None] = '640b23da79b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            'idx_events_event_date',
            table_name='events',
            postgresql_concurrently=True,
            if_exists=True,
        )
