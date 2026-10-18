"""expand events with scheduled_at

Revision ID: d3daa138c1c3
Revises: 75493c93dee7
"""
from alembic import op
import sqlalchemy as sa

revision = "d3daa138c1c3"
down_revision = "75493c93dee7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
