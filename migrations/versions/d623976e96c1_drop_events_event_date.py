"""drop events.event_date

Revision ID: d623976e96c1
Revises: 00b9fff5b77c
Create Date: 2026-07-16 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd623976e96c1'
down_revision = '00b9fff5b77c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('events', 'event_date')


def downgrade() -> None:
    op.add_column('events', sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column('events', 'event_date', nullable=False)
