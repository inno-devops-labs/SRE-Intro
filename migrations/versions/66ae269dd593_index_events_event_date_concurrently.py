"""index events.event_date concurrently

Revision ID: 66ae269dd593
Revises: 5fcf405ffc77
Create Date: 2026-10-12 05:09:47.757329

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66ae269dd593'
down_revision: Union[str, Sequence[str], None] = '5fcf405ffc77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # CONCURRENTLY builds the index under SHARE UPDATE EXCLUSIVE instead of the
    # SHARE lock of a plain CREATE INDEX, so reads and writes keep flowing.
    # Postgres refuses it inside a transaction block, and Alembic runs every
    # migration in one, so step out with autocommit_block().
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
