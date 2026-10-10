"""index events event_date concurrently

Revision ID: cfdc4972afd7
Revises: bd7982ea9e79
Create Date: 2026-07-17 21:10:45.812426

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfdc4972afd7'
down_revision: Union[str, Sequence[str], None] = 'bd7982ea9e79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # CREATE INDEX CONCURRENTLY cannot run inside Alembic's default transaction
    # block (Postgres rejects it with ActiveSqlTransaction). autocommit_block()
    # runs this DDL outside that transaction so CONCURRENTLY actually works.
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
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
