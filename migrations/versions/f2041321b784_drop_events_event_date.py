"""drop events.event_date

Revision ID: f2041321b784
Revises: 01d87284a82f
Create Date: 2026-07-17 21:37:12.532389
"""
from alembic import op
import sqlalchemy as sa

revision = 'f2041321b784'
down_revision = '01d87284a82f'
branch_labels = None
depends_on = None

def upgrade():
    op.drop_column('events', 'event_date')

def downgrade():
    op.add_column('events', sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column('events', 'event_date', nullable=False)