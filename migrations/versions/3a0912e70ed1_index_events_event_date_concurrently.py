"""index events.event_date concurrently

Revision ID: 3a0912e70ed1
Revises: 5fd94f526fb8
Create Date: 2026-09-22 18:41:00.000000

Lab 12 §12.7 — the first migration in this project that cannot run the way
Alembic runs everything else.

Alembic opens a transaction, runs every operation in ``upgrade()`` inside it,
stamps ``alembic_version`` and commits. That is exactly what you want for
ordinary DDL: either the whole revision lands or none of it does. PostgreSQL,
however, refuses ``CREATE INDEX CONCURRENTLY`` inside a transaction block::

    psycopg2.errors.ActiveSqlTransaction:
    CREATE INDEX CONCURRENTLY cannot run inside a transaction block

It refuses because CONCURRENTLY is not one statement — it is a multi-pass
algorithm. Postgres builds the index in two table scans while writers keep
running, waiting in between for every transaction that could still see the old
snapshot to finish. That wait is impossible to express inside the very
transaction that is doing the waiting.

``op.get_context().autocommit_block()`` is Alembic's escape hatch: it commits
the outer transaction, drops the connection into autocommit for the duration of
the block, and opens a fresh transaction afterwards for the version stamp.

The cost of the escape hatch is atomicity, and that is the real reason for
``if_not_exists=True``: if a CONCURRENTLY build is interrupted (cancelled
query, connection drop, pod eviction) Postgres leaves behind an *invalid*
index rather than rolling it back, and there is no transaction to undo it.
The migration has to be safe to run again. ``if_exists=True`` on the downgrade
is the mirror image of the same property.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a0912e70ed1'
down_revision: Union[str, Sequence[str], None] = '5fd94f526fb8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Build an index on ``events(event_date)`` without blocking writers.

    Plain ``CREATE INDEX`` takes a SHARE lock on the table, which blocks every
    INSERT/UPDATE/DELETE for the whole duration of the build (reads still
    work). On QuickTicket's 5-row ``events`` table that is sub-millisecond and the
    difference is invisible. On a 10M-row table it is minutes of writes piling
    up behind the lock, and every connection in the pool blocking behind them
    is how a "harmless index" becomes a full outage.

    CONCURRENTLY takes SHARE UPDATE EXCLUSIVE instead: readers and writers keep
    going, and the price is two table scans instead of one plus the loss of
    transactional rollback.
    """
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Drop the index, also concurrently.

    ``DROP INDEX`` (non-concurrent) takes ACCESS EXCLUSIVE on the table and so
    blocks reads as well as writes. ``DROP INDEX CONCURRENTLY`` has the same
    transaction restriction as the create, hence the same autocommit_block.
    """
    with op.get_context().autocommit_block():
        op.drop_index(
            'idx_events_event_date',
            table_name='events',
            postgresql_concurrently=True,
            if_exists=True,
        )
