"""baseline pre-existing schema

Revision ID: 0bd68c000e8a
Revises:
Create Date: 2026-10-09 21:53:02.569408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0bd68c000e8a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The events and orders tables already exist before Alembic is introduced."""
    pass


def downgrade() -> None:
    """Leave the pre-existing application schema intact."""
    pass
