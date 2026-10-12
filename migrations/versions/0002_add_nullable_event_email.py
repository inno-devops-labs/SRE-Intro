"""Add an optional contact email to events."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("events", "email")
