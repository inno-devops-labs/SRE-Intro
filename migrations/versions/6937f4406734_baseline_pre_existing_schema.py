"""baseline - pre-existing schema

Revision ID: 6937f4406734
Revises: 5085d111d1ce
Create Date: 2026-07-09 13:13:21.662816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6937f4406734'
down_revision: Union[str, Sequence[str], None] = '5085d111d1ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
