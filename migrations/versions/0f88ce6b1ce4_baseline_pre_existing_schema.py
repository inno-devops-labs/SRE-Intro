"""baseline - pre-existing schema

Revision ID: 0f88ce6b1ce4
Revises: 7ae0d8c4d5f2
Create Date: 2026-10-12 07:53:09.166545

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f88ce6b1ce4'
down_revision: Union[str, Sequence[str], None] = '7ae0d8c4d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
