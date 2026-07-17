"""add events scheduled_at

Revision ID: c5c6e038d139
Revises: bc504a88c318
Create Date: 2026-07-17 22:24:09.147520

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5c6e038d139'
down_revision: Union[str, Sequence[str], None] = 'bc504a88c318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "scheduled_at")
