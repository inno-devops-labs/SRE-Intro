"""baseline - pre-existing schema

Revision ID: f654912bc7e1
Revises: 
Create Date: 2026-10-11 16:28:40.866905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f654912bc7e1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Intentionally empty: `events` and `orders` already exist (created by
    # app/seed.sql). This revision was applied with `alembic stamp head`.
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
