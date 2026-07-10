"""baseline - pre-existing schema

Revision ID: fe19828ebd08
Revises: 
Create Date: 2026-07-10 21:25:15.188611

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe19828ebd08'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'email')