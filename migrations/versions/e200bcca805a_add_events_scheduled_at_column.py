"""add events.scheduled_at column

Revision ID: e200bcca805a
Revises: 4d00886794be
Create Date: 2026-10-21 00:13:53.529963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e200bcca805a'
down_revision: Union[str, Sequence[str], None] = '4d00886794be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Expand. Nullable + no default = metadata-only change (no table rewrite,
    # lock held for milliseconds). Old code doesn't know the column exists.
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "scheduled_at")
