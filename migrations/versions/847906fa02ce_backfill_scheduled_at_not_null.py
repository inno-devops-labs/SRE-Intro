"""backfill scheduled_at not null

Revision ID: 847906fa02ce
Revises: c5c6e038d139
Create Date: 2026-07-17 22:27:39.784277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '847906fa02ce'
down_revision: Union[str, Sequence[str], None] = 'c5c6e038d139'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("events", "scheduled_at", nullable=True)
