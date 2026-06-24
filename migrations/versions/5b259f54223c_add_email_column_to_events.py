"""add email column to events

Revision ID: 5b259f54223c
Revises: c1eddc9b7b71
Create Date: 2026-06-24 16:04:22.923072

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b259f54223c'
down_revision: Union[str, Sequence[str], None] = 'c1eddc9b7b71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ –
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'email')
