"""index events.event_date concurrently

Revision ID: 4d00886794be
Revises: be711dc39c96
Create Date: 2026-10-21 00:01:29.457037

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d00886794be'
down_revision: Union[str, Sequence[str], None] = 'be711dc39c96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # CONCURRENTLY builds the index without the ACCESS EXCLUSIVE-style write lock
    # of a plain CREATE INDEX (it takes SHARE UPDATE EXCLUSIVE: reads and writes
    # keep going). Postgres refuses to run it inside a transaction block, and
    # Alembic wraps every migration in one, so step out with autocommit_block().
    # if_not_exists keeps it re-runnable if a previous attempt was interrupted.
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
