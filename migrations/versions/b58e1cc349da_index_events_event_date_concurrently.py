"""index events.event_date concurrently

Revision ID: b58e1cc349da
Revises: 
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b58e1cc349da'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            postgresql_concurrently=True,
            if_not_exists=True
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events', if_exists=True)