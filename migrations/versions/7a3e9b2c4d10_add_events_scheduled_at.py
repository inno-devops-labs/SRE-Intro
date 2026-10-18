"""add events.scheduled_at column

Revision ID: 7a3e9b2c4d10
Revises: 5d1c2e8f9a01
Create Date: 2026-10-18 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a3e9b2c4d10'
down_revision: Union[str, Sequence[str], None] = '5d1c2e8f9a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Expand. Nullable with no default is a catalog-only change in PostgreSQL 11+,
    # so the ACCESS EXCLUSIVE lock is held for milliseconds and no row is
    # rewritten. Code that does not know the column keeps working.
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "scheduled_at")
