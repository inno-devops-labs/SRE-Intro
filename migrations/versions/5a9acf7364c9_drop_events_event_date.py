"""drop events.event_date

Revision ID: 5a9acf7364c9
Revises: 15d87976e4ed
Create Date: 2026-07-18 06:03:59.002953

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a9acf7364c9'
down_revision: Union[str, Sequence[str], None] = '15d87976e4ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("events", sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
