"""add nullable email column to events

Revision ID: c4f91a2d8e76
Revises: 8d3a7c1e4b92
Create Date: 2026-10-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "c4f91a2d8e76"
down_revision: Union[str, Sequence[str], None] = "8d3a7c1e4b92"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '2500ms'")
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column("events", "email")
