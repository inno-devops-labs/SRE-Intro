"""M1: add a nullable destination while retaining the legacy date."""
from alembic import op
import sqlalchemy as sa

revision = '12a0002'
down_revision = '12a0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('events', sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade():
    op.drop_column('events', 'scheduled_at')
