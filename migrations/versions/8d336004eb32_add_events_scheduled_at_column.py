"""add events.scheduled_at column

Revision ID: 8d336004eb32
Revises: 9ccda33933e6
Create Date: 2026-07-04 19:58:57.967994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d336004eb32'
down_revision: Union[str, Sequence[str], None] = '9ccda33933e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Expand step 1: add the new column as NULLABLE. A NOT NULL column with no
    # default fails to add to a table with existing rows; even with a default,
    # a multi-million-row table would take an ACCESS EXCLUSIVE rewrite lock.
    # nullable=True is an instant metadata-only change.
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "scheduled_at")
