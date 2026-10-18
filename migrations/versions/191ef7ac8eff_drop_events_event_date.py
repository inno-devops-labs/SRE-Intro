"""drop events.event_date

Revision ID: 191ef7ac8eff
Revises: 591db45e6159
Create Date: 2026-10-18 18:09:20.227626

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '191ef7ac8eff'
down_revision: Union[str, Sequence[str], None] = '591db45e6159'
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

    op.execute("""
        UPDATE events
        SET event_date = scheduled_at
    """)

    op.alter_column(
        "events",
        "event_date",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )
