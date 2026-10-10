"""add email column to events

Revision ID: 6e6ae2926350
Revises: 8a9b76d6da43
Create Date: 2026-09-26 19:11:32.675552

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e6ae2926350'
down_revision: Union[str, Sequence[str], None] = '8a9b76d6da43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add an optional contact email without rewriting existing rows."""
    op.add_column("events", sa.Column("email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Remove the optional contact email column."""
    op.drop_column("events", "email")
