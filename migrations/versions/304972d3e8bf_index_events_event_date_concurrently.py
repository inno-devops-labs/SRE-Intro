"""index events.event_date concurrently

Revision ID: 304972d3e8bf
Revises: b8891ec9e21a
Create Date: 2026-07-14 14:16:53.027970

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '304972d3e8bf'
down_revision: Union[str, Sequence[str], None] = 'b8891ec9e21a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the index CONCURRENTLY, outside Alembic's default transaction.

    CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so we drop
    out of Alembic's transactional DDL with autocommit_block(). CONCURRENTLY
    takes a milder SHARE UPDATE EXCLUSIVE lock that does NOT block reads/writes,
    so this is safe under live traffic. if_not_exists keeps it re-runnable if a
    previous attempt was interrupted (a CONCURRENTLY build can leave an INVALID
    index behind).
    """
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
    """Drop the index CONCURRENTLY (mirror of upgrade)."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
