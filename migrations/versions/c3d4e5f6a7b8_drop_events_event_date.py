"""drop events.event_date

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-17 22:22:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('events', 'event_date')


def downgrade() -> None:
    op.add_column(
        'events',
        sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column('events', 'event_date', nullable=False)
    # Restore the concurrent index created in 341a3f732deb (dropped with the column).
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
