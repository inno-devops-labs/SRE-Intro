"""M3: contract only after every application reader uses scheduled_at."""
from alembic import op
import sqlalchemy as sa

revision = '12a0004'
down_revision = '12a0003'
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL also removes the index depending on this dropped column.
    op.drop_column('events', 'event_date')


def downgrade():
    op.add_column('events', sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute('UPDATE events SET event_date = scheduled_at')
    op.alter_column('events', 'event_date', nullable=False)
    # Restore the index required by the still-applied 12a0001 revision.
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)
