"""add email column to events

Revision ID: 95710c8a8e44
Revises: 2dcb5d96ea0b
Create Date: 2026-07-10 17:48:52.460277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95710c8a8e44'
down_revision: Union[str, Sequence[str], None] = '2dcb5d96ea0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("events", sa.Column("email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("events", "email")
