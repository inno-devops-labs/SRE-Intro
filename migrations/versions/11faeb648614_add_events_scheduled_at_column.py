"""add events.scheduled_at column

Revision ID: 11faeb648614
Revises: 5764af6d3802
Create Date: 2026-10-09 19:08:49.850575

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11faeb648614'
down_revision: Union[str, Sequence[str], None] = '5764af6d3802'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Expand the schema with a backward-compatible nullable column."""
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove the expanded column while old-code reads remain available."""
    op.drop_column("events", "scheduled_at")
