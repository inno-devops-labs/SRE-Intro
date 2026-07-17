
"""backfill events.scheduled_at

Revision ID: 01d87284a82f
Revises: 1254ab4cdc24
Create Date: 2026-07-17 21:34:26.125674
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision = '01d87284a82f'
down_revision = '1254ab4cdc24'
branch_labels = None
depends_on = None

def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)

def downgrade():
    op.alter_column('events', 'scheduled_at', nullable=True)