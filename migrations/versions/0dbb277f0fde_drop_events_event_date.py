"""drop events.event_date

Revision ID: 0dbb277f0fde
Revises: e915100914fb
Create Date: 2026-07-16 01:33:47.632523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0dbb277f0fde'
down_revision: Union[str, Sequence[str], None] = 'e915100914fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE events SET event_date = scheduled_at WHERE event_date IS NULL"
    )
    op.alter_column("events", "event_date", existing_type=sa.TIMESTAMP(timezone=True), nullable=False)
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
        )
