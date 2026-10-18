"""add nullable scheduled_at column to events

Revision ID: a7c92b1d5e4f
Revises: c1f4e8a92b7d
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c92b1d5e4f"
down_revision: Union[str, Sequence[str], None] = "c1f4e8a92b7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable timezone-aware scheduling column."""
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove only the scheduling column."""
    op.drop_column("events", "scheduled_at")
