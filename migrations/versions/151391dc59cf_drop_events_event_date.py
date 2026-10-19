"""drop events.event_date

Revision ID: 151391dc59cf
Revises: fc9910bf4fbb
Create Date: 2026-10-19 07:30:56.869121

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '151391dc59cf'
down_revision: Union[str, Sequence[str], None] = 'fc9910bf4fbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("events", "event_date")

def downgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "event_date",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE events "
        "SET event_date = scheduled_at"
    )
    op.alter_column(
        "events",
        "event_date",
        nullable=False,
    )
