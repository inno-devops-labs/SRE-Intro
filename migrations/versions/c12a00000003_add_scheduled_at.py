"""Expand events schema with a nullable scheduled_at column and an index."""
from alembic import op
import sqlalchemy as sa

revision = "c12a00000003"
down_revision = "c12a00000002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at", "events", ["scheduled_at"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_scheduled_at", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
    op.drop_column("events", "scheduled_at")
