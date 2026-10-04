"""add email column to events

Revision ID: 9a0002
Revises: 9a0001
Create Date: 2026-10-04 19:10:01+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9a0002"
down_revision: Union[str, Sequence[str], None] = "9a0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "email")
