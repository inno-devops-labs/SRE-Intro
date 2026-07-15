"""add events.scheduled_at column

Revision ID: b12c02
Revises: b12c01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b12c02"
down_revision: Union[str, Sequence[str], None] = "b12c01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
