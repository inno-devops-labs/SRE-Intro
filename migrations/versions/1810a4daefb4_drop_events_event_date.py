"""drop events.event_date

Revision ID: 1810a4daefb4
Revises: 7902d73ca526
Create Date: 2026-09-22 18:46:00.000000

Lab 12 Bonus §12.15 — step 5 of 5 (M1 -> Deploy A -> M2 -> Deploy B -> **M3**).

This is the "contract" half and the only irreversible-in-practice step in the
chain. It is safe here, and ONLY here, because Deploy B is fully rolled out:
``kubectl rollout status deployment/events`` returned, every pod runs the
``lab12b`` image, and no running process names ``event_date`` any more. Run this
one step earlier — before Deploy B has replaced the last Deploy-A pod — and
every request that lands on a surviving Deploy-A pod evaluates
``COALESCE(scheduled_at, event_date)`` against a column that no longer exists,
which is an UndefinedColumn error, a 500 from events, and a 502 at the gateway.

It also carries the index across the rename. Dropping ``event_date`` silently
drops ``idx_events_event_date`` with it, so the equivalent index on
``scheduled_at`` is built first — concurrently, for the same reasons as
revision 3a0912e70ed1. Forgetting this is the classic sequel to a successful
rename: the schema is right, and the ORDER BY that used to be an index scan is
now a sort over the whole table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1810a4daefb4'
down_revision: Union[str, Sequence[str], None] = '7902d73ca526'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c['name'] for c in insp.get_columns('events')}


def upgrade() -> None:
    """Build the replacement index, then drop the old column."""
    # Built outside the transaction (CONCURRENTLY), so it must come first: if
    # the drop below failed we would be left with a harmless extra index, which
    # is a much better failure mode than a dropped column and no index.
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_scheduled_at',
            'events',
            ['scheduled_at'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    if _has_column('event_date'):
        # Catalog-only in PostgreSQL: the column is marked dropped and the old
        # values are reclaimed lazily by later row rewrites. Instant at any
        # table size — but it does take ACCESS EXCLUSIVE, so a production run
        # sets `lock_timeout` and retries rather than queueing behind a long
        # analytics query and blocking every reader in the meantime.
        op.drop_column('events', 'event_date')


def downgrade() -> None:
    """Re-create ``event_date`` and repopulate it from ``scheduled_at``.

    This restores the *schema*, and for this course's data it restores the
    values too, because ``scheduled_at`` is a faithful copy that nothing has
    diverged from. See submissions/lab12.md for why that is NOT the same thing
    as a safe production rollback once Deploy B has been taking writes.
    """
    if not _has_column('event_date'):
        op.add_column(
            'events',
            sa.Column('event_date', sa.TIMESTAMP(timezone=True), nullable=True),
        )
        op.execute("UPDATE events SET event_date = scheduled_at")
        op.alter_column('events', 'event_date', nullable=False)

    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            'idx_events_scheduled_at',
            table_name='events',
            postgresql_concurrently=True,
            if_exists=True,
        )
