"""backfill and require events scheduled_at

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
    )
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "events",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
