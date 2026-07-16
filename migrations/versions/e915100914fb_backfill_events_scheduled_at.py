"""backfill events.scheduled_at

Revision ID: e915100914fb
Revises: 766eda1b610e
Create Date: 2026-07-16 01:33:47.611357

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e915100914fb'
down_revision: Union[str, Sequence[str], None] = '766eda1b610e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column("events", "scheduled_at", existing_type=sa.TIMESTAMP(timezone=True), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("events", "scheduled_at", existing_type=sa.TIMESTAMP(timezone=True), nullable=True)
