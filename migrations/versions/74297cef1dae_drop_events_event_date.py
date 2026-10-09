"""drop events.event_date

Revision ID: 74297cef1dae
Revises: bf079646f310
Create Date: 2026-10-09 19:18:26.235730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '74297cef1dae'
down_revision: Union[str, Sequence[str], None] = 'bf079646f310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Contract the schema after all application pods use scheduled_at."""
    op.drop_column("events", "event_date")


def downgrade() -> None:
    """Recreate and repopulate the legacy column for schema rollback."""
    op.add_column(
        "events",
        sa.Column("event_date", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column(
        "events",
        "event_date",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
