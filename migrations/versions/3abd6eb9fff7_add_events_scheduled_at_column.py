"""add events.scheduled_at column

Revision ID: 3abd6eb9fff7
Revises: 83c468f105dd
Create Date: 2026-10-19 02:41:03.138551

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3abd6eb9fff7'
down_revision: Union[str, Sequence[str], None] = '83c468f105dd'
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
