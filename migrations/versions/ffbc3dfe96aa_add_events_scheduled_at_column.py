"""add events.scheduled_at column

Revision ID: ffbc3dfe96aa
Revises: e999114930be
Create Date: 2026-07-18 05:59:39.452731

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ffbc3dfe96aa'
down_revision: Union[str, Sequence[str], None] = 'e999114930be'
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
