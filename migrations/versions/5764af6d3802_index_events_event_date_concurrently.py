"""index events.event_date concurrently

Revision ID: 5764af6d3802
Revises: a5400e67ce10
Create Date: 2026-10-09 19:04:16.540824

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5764af6d3802'
down_revision: Union[str, Sequence[str], None] = 'a5400e67ce10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add an online index without blocking application reads or writes."""
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Remove the index without taking a long blocking lock."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
