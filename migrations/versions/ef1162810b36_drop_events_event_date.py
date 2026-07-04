"""drop events.event_date

Revision ID: ef1162810b36
Revises: b2b755d1be72
Create Date: 2026-07-04 19:58:58.255457

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef1162810b36'
down_revision: Union[str, Sequence[str], None] = 'b2b755d1be72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Contract step: safe NOW (not earlier) because Deploy B is fully rolled out
    # and no longer reads OR writes event_date. If a stray Deploy-A pod still ran
    # its COALESCE(scheduled_at, event_date), it would 500 on the missing column.
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
