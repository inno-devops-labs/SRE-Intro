"""backfill events.scheduled_at

Revision ID: bf079646f310
Revises: 11faeb648614
Create Date: 2026-10-09 19:17:07.335595

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf079646f310'
down_revision: Union[str, Sequence[str], None] = '11faeb648614'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill existing rows, then enforce the new-column invariant."""
    op.execute(
        "UPDATE events SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    """Allow the expanded column to become nullable again."""
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=True,
    )
