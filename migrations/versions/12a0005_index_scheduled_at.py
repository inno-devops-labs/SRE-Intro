"""Index the final date; keep the already-applied legacy index migration intact."""
from alembic import op

revision = '12a0005'
down_revision = '12a0004'
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index('idx_events_scheduled_at', 'events', ['scheduled_at'],
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_scheduled_at', table_name='events',
                      postgresql_concurrently=True, if_exists=True)
