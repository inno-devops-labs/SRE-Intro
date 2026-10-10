"""Add an optional events contact email without changing existing API contracts."""

from alembic import op
import sqlalchemy as sa


revision = "0002_events_email"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "email")
