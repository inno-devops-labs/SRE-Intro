"""Expand the event schema while the legacy reader is still deployed."""

from alembic import op
import sqlalchemy as sa


revision = "0004_add_scheduled_at"
down_revision = "0003_event_date_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
