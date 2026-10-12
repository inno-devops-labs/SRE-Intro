"""drop events.event_date

Revision ID: ec4613a31045
Revises: 41d677444ecf
Create Date: 2026-10-12 05:10:55.635859

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec4613a31045'
down_revision: Union[str, Sequence[str], None] = '41d677444ecf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Contract: only after Deploy B (no reads or writes of event_date) is fully
    # rolled out. Also drops idx_events_event_date; idx_events_scheduled_at
    # (migration 1) replaces it.
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
