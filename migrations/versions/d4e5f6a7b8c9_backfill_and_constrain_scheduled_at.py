"""backfill and constrain events.scheduled_at

Revision ID: d4e5f6a7b8c9
Revises: a7c92b1d5e4f
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "a7c92b1d5e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill scheduled times and require a value for every event."""
    op.execute(
        sa.text(
            "UPDATE events "
            "SET scheduled_at = event_date "
            "WHERE scheduled_at IS NULL"
        )
    )
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    """Allow scheduled_at to be nullable again without changing its values."""
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
