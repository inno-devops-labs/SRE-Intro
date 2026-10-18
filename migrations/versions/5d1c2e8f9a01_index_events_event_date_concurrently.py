"""index events.event_date concurrently

Revision ID: 5d1c2e8f9a01
Revises: 43b43772c5f3
Create Date: 2026-10-18 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d1c2e8f9a01'
down_revision: Union[str, Sequence[str], None] = '43b43772c5f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # CREATE INDEX CONCURRENTLY refuses to run inside a transaction block, and
    # Alembic wraps every migration in one. autocommit_block() commits that
    # transaction, runs the DDL in autocommit mode and opens a new one after.
    # if_not_exists keeps a re-run safe after an interrupted build.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
