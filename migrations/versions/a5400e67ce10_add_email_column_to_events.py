"""add email column to events

Revision ID: a5400e67ce10
Revises: 7a85fcd6631b
Create Date: 2026-10-09 13:24:13.247042

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5400e67ce10'
down_revision: Union[str, Sequence[str], None] = '7a85fcd6631b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add an optional contact email without rewriting existing rows."""
    op.add_column(
        "events",
        sa.Column("email", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove the optional contact email."""
    op.drop_column("events", "email")
