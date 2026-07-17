"""index events.event_date concurrently

Revision ID: de677748fa9d
Revises: 
Create Date: 2026-07-17 21:37:14.391124

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de677748fa9d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create an index on events(event_date) using CONCURRENTLY.

    CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and Alembic
    wraps migrations in a transaction by default — so we open an autocommit block
    for the DDL. if_not_exists keeps the migration re-runnable if interrupted.
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
    """Drop the index (mirror of upgrade, also concurrently / if_exists)."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
