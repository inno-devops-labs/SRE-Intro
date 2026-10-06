"""index events.event_date concurrently

Revision ID: 5e0902edb4c9
Revises: cf66c14f522d
Create Date: 2026-10-06 11:47:44.930580

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e0902edb4c9'
down_revision: Union[str, Sequence[str], None] = 'cf66c14f522d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so it
    # must run outside Alembic's default transactional DDL wrapper.
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events', if_exists=True)
