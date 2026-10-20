"""backfill events.scheduled_at

Revision ID: e20694da4601
Revises: e200bcca805a
Create Date: 2026-10-21 00:13:53.913032

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e20694da4601'
down_revision: Union[str, Sequence[str], None] = 'e200bcca805a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Backfill. Safe under live traffic: Deploy A reads COALESCE(scheduled_at,
    # event_date), so NULL and filled rows look the same to it. Idempotent
    # thanks to WHERE ... IS NULL. (5 rows here; on a big table do it in small
    # batches, one transaction each — see submissions/lab12.md.)
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    # SET NOT NULL scans the table under ACCESS EXCLUSIVE. Instant for 5 rows;
    # on a big table add CHECK (scheduled_at IS NOT NULL) NOT VALID, VALIDATE it,
    # then SET NOT NULL (Postgres 12+ reuses the validated check, no scan).
    op.alter_column("events", "scheduled_at", nullable=False)
    # Deploy B sorts by scheduled_at, and M3 drops event_date together with
    # idx_events_event_date (Lab 12.7) — build the replacement index first.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at",
            "events",
            ["scheduled_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_scheduled_at",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
    # No UPDATE back: event_date still has the data.
    op.alter_column("events", "scheduled_at", nullable=True)
