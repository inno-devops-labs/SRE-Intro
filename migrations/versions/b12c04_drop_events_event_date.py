"""drop events.event_date

Revision ID: b12c04
Revises: b12c03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b12c04"
down_revision: Union[str, Sequence[str], None] = "b12c03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
