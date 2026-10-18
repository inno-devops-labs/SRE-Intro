"""add concurrent index on events event_date

Revision ID: 75493c93dee7
Revises: 7e0f8d34fcbe
"""
from typing import Sequence, Union

from alembic import op

revision: str = "75493c93dee7"
down_revision: Union[str, Sequence[str], None] = "7e0f8d34fcbe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_events_event_date "
            "ON events (event_date)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_events_event_date")
