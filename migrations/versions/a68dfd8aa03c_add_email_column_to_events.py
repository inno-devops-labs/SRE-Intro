"""add email column to events

Revision ID: a68dfd8aa03c
Revises: 1536dcffc4c9
Create Date: 2026-10-12 09:10:48.988733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a68dfd8aa03c'
down_revision: Union[str, Sequence[str], None] = '1536dcffc4c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("email", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "email")
