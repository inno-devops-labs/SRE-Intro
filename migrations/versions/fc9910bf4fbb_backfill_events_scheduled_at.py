"""backfill events.scheduled_at

Revision ID: fc9910bf4fbb
Revises: a20077681b92
Create Date: 2026-10-19 07:25:38.906151

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fc9910bf4fbb'
down_revision: Union[str, Sequence[str], None] = 'a20077681b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE events "
        "SET scheduled_at = event_date "
        "WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        nullable=False,
    )

def downgrade() -> None:
    op.alter_column(
        "events",
        "scheduled_at",
        nullable=True,
    )
