"""add email column to events

Revision ID: bd7982ea9e79
Revises: 0297af066379
Create Date: 2026-07-03 18:11:37.853994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bd7982ea9e79'
down_revision: Union[str, Sequence[str], None] = '0297af066379'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("email", sa.String(255), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("events", "email")