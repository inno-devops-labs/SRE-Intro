"""index events.event_date concurrently

Revision ID: e999114930be
Revises: 7c64784cebc3
Create Date: 2026-07-18 05:55:11.437378

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e999114930be'
down_revision: Union[str, Sequence[str], None] = '7c64784cebc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
