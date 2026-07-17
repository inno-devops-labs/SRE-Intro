"""backfill events.scheduled_at

Revision ID: a12f03000003
Revises: a12f02000002
Create Date: 2026-07-17 12:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a12f03000003"
down_revision: Union[str, Sequence[str], None] = "a12f02000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("events", "scheduled_at", nullable=True)
