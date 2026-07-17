"""add email column to events

Revision ID: a8a72f006699
Revises: 
Create Date: 2026-07-10 23:41:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8a72f006699'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('email', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
