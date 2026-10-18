"""contract drop event_date

Revision ID: 03465bf07988
Revises: 63b7037f00b4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "03465bf07988"
down_revision: Union[str, Sequence[str], None] = "63b7037f00b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("events", "event_date")


def downgrade() -> None:
    op.add_column(
        "events",
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column("events", "event_date", nullable=False)
