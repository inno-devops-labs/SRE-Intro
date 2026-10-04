"""add events.scheduled_at column

Revision ID: ed097f0d82b9
Revises: 304972d3e8bf
Create Date: 2026-07-14 14:22:30.076768

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ed097f0d82b9'
down_revision: Union[str, Sequence[str], None] = '304972d3e8bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Expand: add the new column, nullable so it is instant on a populated table.

    A NOT NULL column with no default would fail to add to a table with existing
    rows; even with a default, on a large table the rewrite takes an ACCESS
    EXCLUSIVE lock. nullable=True is a metadata-only change — safe under traffic.
    """
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
