"""add events.scheduled_at column

Revision ID: 766eda1b610e
Revises: 486a4f25cc47
Create Date: 2026-07-16 01:33:47.591039

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '766eda1b610e'
down_revision: Union[str, Sequence[str], None] = '486a4f25cc47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "scheduled_at")
