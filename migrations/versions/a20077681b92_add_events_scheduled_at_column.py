"""add events.scheduled_at column

Revision ID: a20077681b92
Revises: 398e147b49ca
Create Date: 2026-10-19 07:12:56.135615

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a20077681b92'
down_revision: Union[str, Sequence[str], None] = '398e147b49ca'
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
