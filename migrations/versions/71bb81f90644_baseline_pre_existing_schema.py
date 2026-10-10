"""baseline - pre-existing schema

Revision ID: 71bb81f90644
Revises: 
Create Date: 2026-07-10 13:58:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '71bb81f90644'
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
