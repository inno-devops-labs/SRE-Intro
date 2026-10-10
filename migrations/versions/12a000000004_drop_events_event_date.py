"""Contract events to scheduled_at after Deploy B is fully rolled out.

Revision ID: 12a000000004
Revises: 12a000000003
"""
from alembic import op
import sqlalchemy as sa

revision = "12a000000004"
down_revision = "12a000000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep date indexing after the old column and its index are removed.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at",
            "events",
            ["scheduled_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column(
        "events",
        "event_date",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
    # Restore the index required by the preceding migration revision.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "idx_events_scheduled_at",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
