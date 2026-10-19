"""drop events.event_date

Revision ID: 6c2154cb9ed7
Revises: 6c15edff59d9
Create Date: 2026-10-19 02:46:12.051934

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c2154cb9ed7'
down_revision: Union[str, Sequence[str], None] = '6c15edff59d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("events", "event_date")



def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
