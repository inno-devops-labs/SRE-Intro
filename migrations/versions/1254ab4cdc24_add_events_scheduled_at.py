"""add events.scheduled_at column

Revision ID: 1254ab4cdc24
Revises: 2ec7abf68626
Create Date: 2026-07-17 21:32:54.237485
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1254ab4cdc24'
down_revision = '2ec7abf68626'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('events',
                  sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))

def downgrade():
    op.drop_column('events', 'scheduled_at')