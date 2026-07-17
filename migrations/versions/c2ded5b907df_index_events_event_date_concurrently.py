"""index events.event_date concurrently

Revision ID: c2ded5b907df
Revises: a8a72f006699
Create Date: 2026-07-16 14:52:47.256399

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2ded5b907df'
down_revision = 'a8a72f006699'
branch_labels = None
depends_on = None


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
    op.drop_index('idx_events_event_date', table_name='events', if_exists=True)
