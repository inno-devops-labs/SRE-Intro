"""drop events.event_date

Revision ID: 70b3c0332518
Revises: 716413418772
Create Date: 2026-07-17 21:52:28.277994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70b3c0332518'
down_revision: Union[str, Sequence[str], None] = '716413418772'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Contract: drop the old column now that Deploy B neither reads nor writes it.

    Safe ONLY here: Deploy B is fully rolled out, so no live pod references
    event_date. (Dropping the column also drops idx_events_event_date from 12.7,
    since an index cannot outlive its column.) Running this before Deploy B was
    complete would 500 every /events request on any surviving Deploy-A pod, whose
    COALESCE still names event_date.
    """
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
