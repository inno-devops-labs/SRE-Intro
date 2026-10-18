"""Backfill after deploy A; preserve the date index on the new column."""
from alembic import op

revision = 'b12000000003'
down_revision = 'b12000000002'
branch_labels = depends_on = None


def upgrade():
    # Tiny lab table. Large installations need bounded batches and a validated
    # CHECK constraint before SET NOT NULL, rather than one long transaction.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute('UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL')
    op.alter_column('events', 'scheduled_at', nullable=False)
    with op.get_context().autocommit_block():
        op.create_index('idx_events_scheduled_at', 'events', ['scheduled_at'],
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_scheduled_at', table_name='events',
                      postgresql_concurrently=True, if_exists=True)
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.alter_column('events', 'scheduled_at', nullable=True)
