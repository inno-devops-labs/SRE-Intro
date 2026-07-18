"""index events.event_date concurrently

Revision ID: a06ff4ace509
Revises: 80e41ae35bfd
Create Date: 2026-07-18
"""

from typing import Union

from alembic import op


revision: str = "a06ff4ace509"
down_revision: Union[str, None] = "80e41ae35bfd"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
