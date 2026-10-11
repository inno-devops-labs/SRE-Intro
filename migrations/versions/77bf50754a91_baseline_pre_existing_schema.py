"""baseline - pre-existing schema

events and orders are created by app/seed.sql (mounted into the postgres
initdb directory by k8s/postgres.yaml). This revision changes nothing; it is
stamped onto the existing database so later revisions have a parent.

Revision ID: 77bf50754a91
Revises: 
Create Date: 2026-10-11 19:03:53.073966

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '77bf50754a91'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
