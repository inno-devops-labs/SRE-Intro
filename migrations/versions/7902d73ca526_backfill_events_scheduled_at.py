"""backfill events.scheduled_at

Revision ID: 7902d73ca526
Revises: 25b2cce54ede
Create Date: 2026-09-22 18:44:00.000000

Lab 12 Bonus §12.13 — step 3 of 5 (M1 -> Deploy A -> **M2** -> Deploy B -> M3).

Safe under live traffic because Deploy A is already everywhere, and Deploy A
reads ``COALESCE(scheduled_at, event_date)``: it is correct whether a given row
has been backfilled or not, so there is no moment during this UPDATE at which a
request can observe a broken state. Running M2 *before* Deploy A would have
been equally harmless (nothing read the column yet) — M2 is the one step in the
sequence whose position is flexible. M1, Deploy B and M3 are not.

The guard on ``event_date`` exists because this project has two independent
sources of the same schema: the Alembic chain, and the bootstrap
``seed.sql`` / postgres ConfigMap that a freshly created PV replays. After the
bonus, the seed creates ``scheduled_at`` directly, so on a fresh cluster this
revision has nothing to copy from and must be a no-op rather than an error.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7902d73ca526'
down_revision: Union[str, Sequence[str], None] = '25b2cce54ede'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c['name'] for c in insp.get_columns('events')}


def upgrade() -> None:
    """Copy ``event_date`` into ``scheduled_at``, then tighten to NOT NULL.

    Order matters: the NOT NULL constraint can only be added once every row has
    a value, so the UPDATE and the ALTER belong in the same revision, in this
    order. Splitting them into two revisions would create a window in which the
    deploy could stop half-way with a populated but still-nullable column —
    harmless, but pointless extra state.

    The ``WHERE scheduled_at IS NULL`` predicate makes the backfill idempotent:
    re-running the revision after a partial failure copies only what is still
    missing, and never overwrites a value a newer writer has already put there.

    At QuickTicket's scale (5 rows) this is a single instant statement. At 10M
    rows a single UPDATE would hold row locks and inflate the table by 10M dead
    tuples inside one transaction, blocking autovacuum and stalling writers for
    minutes — see the batching pattern in submissions/lab12.md.
    """
    if _has_column('event_date'):
        op.execute(
            "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
        )
    op.alter_column('events', 'scheduled_at', nullable=False)


def downgrade() -> None:
    """Relax the constraint again.

    No data is copied back: ``event_date`` still holds every value at this
    point in the chain (M3 has not run, or has itself been downgraded first),
    so the old column is still the source of truth and ``scheduled_at`` can
    simply be left populated.
    """
    op.alter_column('events', 'scheduled_at', nullable=True)
