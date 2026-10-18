"""add events.scheduled_at column

Revision ID: 25b2cce54ede
Revises: 3a0912e70ed1
Create Date: 2026-09-22 18:42:00.000000

Lab 12 Bonus §12.11 — step 1 of 5 of the expand-and-contract rename of
``events.event_date`` to ``events.scheduled_at``.

    M1 (this)  ->  Deploy A  ->  M2  ->  Deploy B  ->  M3

This is the "expand" half: the table temporarily carries BOTH names. Nothing
reads or writes the new column yet, so this revision is a no-op from the
application's point of view and is trivially reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25b2cce54ede'
down_revision: Union[str, Sequence[str], None] = '3a0912e70ed1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``scheduled_at`` as a NULLABLE column with no default.

    nullable=True is load-bearing, not laziness:

    * A NOT NULL column with no default simply fails on a table that already
      has rows — there is no value to put in them.
    * NOT NULL *with* a default is accepted, but on PostgreSQL < 11 (and on any
      version when the default is volatile) it rewrites every row while holding
      ACCESS EXCLUSIVE. On a 10M-row table that is minutes during which every
      SELECT on ``events`` blocks. This is one of the classic "the migration
      was instant in staging" outages.
    * A nullable column with no default is a catalog-only change: Postgres
      writes one pg_attribute row and is done. The ACCESS EXCLUSIVE lock is
      held for microseconds — long enough to matter only if a long-running
      query is already holding a conflicting lock (which is why production
      migrations set ``lock_timeout``).

    The column becomes NOT NULL in M2, *after* it has been backfilled.
    """
    op.add_column(
        'events',
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the column again. Safe at this point precisely because no deployed
    code reads or writes it yet — that only becomes true after Deploy A."""
    op.drop_column('events', 'scheduled_at')
