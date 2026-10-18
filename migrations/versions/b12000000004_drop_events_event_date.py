"""Contract only after every events pod runs deploy B."""
from alembic import op
import sqlalchemy as sa

revision = 'b12000000004'
down_revision = 'b12000000003'
branch_labels = depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '3s'")
    # PostgreSQL also removes idx_events_event_date, which depends on this column.
    op.drop_column('events', 'event_date')


def downgrade():
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column('events', sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute('UPDATE events SET event_date = scheduled_at')
    op.alter_column('events', 'event_date', nullable=False)
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)
