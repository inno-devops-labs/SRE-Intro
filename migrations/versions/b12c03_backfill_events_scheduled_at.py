"""backfill events.scheduled_at

Revision ID: b12c03
Revises: b12c02
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b12c03"
down_revision: Union[str, Sequence[str], None] = "b12c02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    op.alter_column("events", "scheduled_at", nullable=True)
