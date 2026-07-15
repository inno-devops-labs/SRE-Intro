"""index events.event_date concurrently

Revision ID: b12c01
Revises: adcc81d29aa3
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b12c01"
down_revision: Union[str, Sequence[str], None] = "adcc81d29aa3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
