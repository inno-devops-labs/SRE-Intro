"""add events.scheduled_at column

Revision ID: 62e2f6d2502b
Revises: de677748fa9d
Create Date: 2026-07-17 21:47:31.689738

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62e2f6d2502b'
down_revision: Union[str, Sequence[str], None] = 'de677748fa9d'
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
