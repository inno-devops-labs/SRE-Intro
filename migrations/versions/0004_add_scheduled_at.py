"""Expand: add nullable replacement column."""
from alembic import op
import sqlalchemy as sa
revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None

def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))

def downgrade():
    op.drop_column("events", "scheduled_at")
