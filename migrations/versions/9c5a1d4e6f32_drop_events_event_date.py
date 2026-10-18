"""drop events.event_date

Revision ID: 9c5a1d4e6f32
Revises: 8b4f0c3d5e21
Create Date: 2026-10-18 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c5a1d4e6f32'
down_revision: Union[str, Sequence[str], None] = '8b4f0c3d5e21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Contract. Only after Deploy B is fully rolled out: a Deploy A pod still
    # selects COALESCE(scheduled_at, event_date) and would get
    # 'column "event_date" does not exist' on every /events request.
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
