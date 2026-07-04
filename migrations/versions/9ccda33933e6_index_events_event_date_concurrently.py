"""index events.event_date concurrently

Revision ID: 9ccda33933e6
Revises: 6dac6ce054b1
Create Date: 2026-07-04 19:58:57.823166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ccda33933e6'
down_revision: Union[str, Sequence[str], None] = '6dac6ce054b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and
    # Alembic wraps migrations in a transaction by default — so we open an
    # autocommit block. CONCURRENTLY takes only a SHARE UPDATE EXCLUSIVE lock
    # (doesn't block reads/writes); the plain form would take ACCESS EXCLUSIVE
    # for the whole build — minutes of blocked queries on a 10M-row table.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,      # re-runnable if a build was interrupted
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
