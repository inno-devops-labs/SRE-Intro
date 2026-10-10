"""add events event_date index concurrently

Revision ID: b7c4d8e91f20
Revises: 6e6ae2926350
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b7c4d8e91f20"
down_revision: Union[str, None] = "6e6ae2926350"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            if_exists=True,
            postgresql_concurrently=True,
        )
