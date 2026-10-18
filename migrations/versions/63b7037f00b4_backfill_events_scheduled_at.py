"""backfill events scheduled_at

Revision ID: 63b7037f00b4
Revises: d3daa138c1c3
"""
from alembic import op

revision = "63b7037f00b4"
down_revision = "d3daa138c1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
            batch_size INTEGER := 10000;
            rows_updated INTEGER;
        BEGIN
            LOOP
                UPDATE events
                SET scheduled_at = event_date
                WHERE id IN (
                    SELECT id
                    FROM events
                    WHERE scheduled_at IS NULL
                    LIMIT batch_size
                );

                GET DIAGNOSTICS rows_updated = ROW_COUNT;
                EXIT WHEN rows_updated = 0;
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    op.execute("UPDATE events SET scheduled_at = NULL")
