"""drop events.event_date

Revision ID: 77526c98f7f3
Revises: b4d051a85a3f
Create Date: 2026-10-06 11:55:30.314704

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '77526c98f7f3'
down_revision: Union[str, Sequence[str], None] = 'b4d051a85a3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Contract: safe ONLY now, because Deploy B is fully rolled out and no
    # code reads or writes event_date anymore. Any surviving Deploy-A pod
    # would 500 on every /events request (COALESCE on a missing column).
    op.drop_column('events', 'event_date')


def downgrade() -> None:
    op.add_column(
        'events',
        sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE events SET event_date = scheduled_at")
    op.alter_column('events', 'event_date', nullable=False)
