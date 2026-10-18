"""drop events.event_date

Revision ID: aadf3fbabc16
Revises: 0b99dd9e96c4
Create Date: 2026-10-18 15:09:45.333596

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aadf3fbabc16'
down_revision: Union[str, Sequence[str], None] = '0b99dd9e96c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('events', 'event_date')


def downgrade() -> None:
    op.add_column('events', sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column('events', 'event_date', nullable=False)
