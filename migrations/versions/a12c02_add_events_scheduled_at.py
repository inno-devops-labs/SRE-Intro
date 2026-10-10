"""Add events.scheduled_at as a nullable expansion column."""

from alembic import op
import sqlalchemy as sa

revision = "a12c02"
down_revision = "a12c01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("events", "scheduled_at")
