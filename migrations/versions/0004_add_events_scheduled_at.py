"""Expand: allow old and new event date readers to coexist."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("events", "scheduled_at")
