"""Build the legacy event-date index outside a transaction."""
from alembic import op

revision = '12a0001'
down_revision = '9a0002'
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events',
                      postgresql_concurrently=True, if_exists=True)
