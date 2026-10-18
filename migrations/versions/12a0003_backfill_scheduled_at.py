"""M2: backfill only missing values, then enforce the destination constraint."""
from alembic import op

revision = '12a0003'
down_revision = '12a0002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL')
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade():
    op.alter_column('events', 'scheduled_at', nullable=True)
