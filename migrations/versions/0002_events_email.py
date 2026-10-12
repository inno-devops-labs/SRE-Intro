"""Add optional email without backfill or table rewrite."""
from alembic import op
import sqlalchemy as sa
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    # ALTER TABLE still needs ACCESS EXCLUSIVE; bound lock waiting under load.
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))

def downgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.drop_column("events", "email")
