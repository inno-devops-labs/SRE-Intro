"""add orders event_id index concurrently

Revision ID: 004_add_orders_event_id_index
Revises: 729c84a02c1a
Create Date: 2026-07-17 14:00:00.000000

"""
from typing import Sequence, Union

import psycopg2
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004_add_orders_event_id_index'
down_revision: Union[str, Sequence[str], None] = '729c84a02c1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_raw_connection():
    url = op.get_bind().engine.url
    conn = psycopg2.connect(str(url))
    conn.autocommit = True
    return conn


def upgrade() -> None:
    conn = _get_raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("CREATE INDEX CONCURRENTLY idx_orders_event_id ON orders (event_id)")
        cur.close()
    finally:
        conn.close()


def downgrade() -> None:
    conn = _get_raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("DROP INDEX CONCURRENTLY idx_orders_event_id")
        cur.close()
    finally:
        conn.close()
