"""Expand: old code remains compatible; deploy A after this revision."""
from alembic import op
import sqlalchemy as sa

revision = 'b12000000002'
down_revision = 'b12000000001'
branch_labels = depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade():
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column('events', 'scheduled_at')
