"""drop events.event_date

Revision ID: 85eeb559c536
Revises: e20694da4601
Create Date: 2026-10-21 00:13:54.290869

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '85eeb559c536'
down_revision: Union[str, Sequence[str], None] = 'e20694da4601'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Contract. Only after Deploy B is fully rolled out: no running code reads
    # or writes event_date any more. Dropping the column also drops
    # idx_events_event_date. A Deploy A pod still alive here would 500 on every
    # /events (its COALESCE references the missing column).
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
