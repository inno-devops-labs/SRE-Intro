"""add events.scheduled_at column

Revision ID: e592792b61c0
Revises: c2ded5b907df
Create Date: 2026-07-16 14:54:49.025046

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e592792b61c0'
down_revision = 'c2ded5b907df'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'scheduled_at')
