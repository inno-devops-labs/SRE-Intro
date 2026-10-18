"""drop legacy events.event_date after the scheduled_at cutover

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the legacy event_date column after Deploy B is live."""
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Recreate event_date and restore values from scheduled_at."""
    op.add_column(
        "events",
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE events "
            "SET event_date = scheduled_at "
            "WHERE event_date IS NULL"
        )
    )
    op.alter_column(
        "events",
        "event_date",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
