"""backfill events.scheduled_at

Revision ID: 0b99dd9e96c4
Revises: 64f12464912c
Create Date: 2026-10-18 15:09:45.138872

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b99dd9e96c4'
down_revision: Union[str, Sequence[str], None] = '64f12464912c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade() -> None:
    op.alter_column('events', 'scheduled_at', nullable=True)
