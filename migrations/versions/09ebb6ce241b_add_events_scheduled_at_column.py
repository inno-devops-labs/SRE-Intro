"""add events.scheduled_at column

Revision ID: 09ebb6ce241b
Revises: 5e0902edb4c9
Create Date: 2026-10-06 11:48:35.007893

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '09ebb6ce241b'
down_revision: Union[str, Sequence[str], None] = '5e0902edb4c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand: add the new column as NULLABLE. A NOT NULL column (even with a
    # default) would force a full table rewrite under an ACCESS EXCLUSIVE lock;
    # a nullable add is metadata-only and instant.
    op.add_column(
        'events',
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('events', 'scheduled_at')
