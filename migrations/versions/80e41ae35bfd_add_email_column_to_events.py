"""add email column to events

Revision ID: 80e41ae35bfd
Revises: 71bb81f90644
Create Date: 2026-07-10 14:00:00.000000

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '80e41ae35bfd'
down_revision: Union[str, None] = '71bb81f90644'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
