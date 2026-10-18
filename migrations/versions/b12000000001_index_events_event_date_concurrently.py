"""Index the existing date without blocking writes."""
from alembic import op

revision = 'b12000000001'
down_revision = 'a68dfd8aa03c'
branch_labels = depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events',
                      postgresql_concurrently=True, if_exists=True)
