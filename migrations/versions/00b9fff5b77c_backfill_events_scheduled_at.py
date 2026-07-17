"""backfill events.scheduled_at

Revision ID: 00b9fff5b77c
Revises: e592792b61c0
Create Date: 2026-07-16 14:58:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '00b9fff5b77c'
down_revision = 'e592792b61c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade() -> None:
    op.alter_column('events', 'scheduled_at', nullable=True)
