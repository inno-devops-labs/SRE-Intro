"""index events.event_date concurrently

Revision ID: 3566ff6cdbcf
Revises: 56780f89edf0
Create Date: 2026-07-09 20:17:19.460871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3566ff6cdbcf'
down_revision: Union[str, Sequence[str], None] = '56780f89edf0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and Alembic
    # wraps migrations in one by default — autocommit_block() runs the DDL outside it.
    # CONCURRENTLY takes only a SHARE UPDATE EXCLUSIVE lock (reads + writes keep
    # working) instead of the ACCESS EXCLUSIVE lock a plain CREATE INDEX takes.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,   # re-runnable if a previous attempt was interrupted
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
