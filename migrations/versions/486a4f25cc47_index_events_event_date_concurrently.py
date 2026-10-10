"""index events.event_date concurrently

Revision ID: 486a4f25cc47
Revises: 95710c8a8e44
Create Date: 2026-07-16 01:33:47.581083

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '486a4f25cc47'
down_revision: Union[str, Sequence[str], None] = '95710c8a8e44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            if_exists=True,
            postgresql_concurrently=True,
        )
