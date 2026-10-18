"""index events.event_date concurrently

Revision ID: a1c2d3e4f5b6
Revises: 5fcf405ffc77
Create Date: 2026-10-19 00:00:00.000000
"""
from alembic import op

revision = "a1c2d3e4f5b6"
down_revision = "5fcf405ffc77"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
