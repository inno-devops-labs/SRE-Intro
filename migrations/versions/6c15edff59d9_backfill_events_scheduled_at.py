"""backfill events.scheduled_at

Revision ID: 6c15edff59d9
Revises: 3abd6eb9fff7
Create Date: 2026-10-19 02:43:46.707894

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c15edff59d9'
down_revision: Union[str, Sequence[str], None] = '3abd6eb9fff7'
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
