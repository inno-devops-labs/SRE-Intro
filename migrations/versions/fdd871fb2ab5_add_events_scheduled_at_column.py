"""add events.scheduled_at column

Revision ID: fdd871fb2ab5
Revises: 5769432bac65
Create Date: 2026-10-18 15:38:11.219994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdd871fb2ab5'
down_revision: Union[str, Sequence[str], None] = '5769432bac65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "scheduled_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
