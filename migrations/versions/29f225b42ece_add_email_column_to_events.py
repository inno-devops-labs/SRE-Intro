"""add email column to events

Revision ID: 29f225b42ece
Revises: 4ba79a665e06
Create Date: 2026-10-11 04:53:01.973687

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29f225b42ece'
down_revision: Union[str, Sequence[str], None] = '4ba79a665e06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "email")
