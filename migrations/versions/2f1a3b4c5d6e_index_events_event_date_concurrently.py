"""index events.event_date concurrently

Revision ID: 2f1a3b4c5d6e
Revises: c35d23d6cab6
Create Date: 2026-07-17 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2f1a3b4c5d6e"
down_revision: Union[str, Sequence[str], None] = "c35d23d6cab6"
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
