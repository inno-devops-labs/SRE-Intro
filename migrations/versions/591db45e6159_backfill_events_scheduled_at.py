"""backfill events.scheduled_at

Revision ID: 591db45e6159
Revises: fdd871fb2ab5
Create Date: 2026-10-18 17:43:13.181575

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '591db45e6159'
down_revision: Union[str, Sequence[str], None] = 'fdd871fb2ab5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade() -> None:
    op.execute("""
        UPDATE events
        SET scheduled_at = event_date
        WHERE scheduled_at IS NULL
    """)

    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=True,
    )

