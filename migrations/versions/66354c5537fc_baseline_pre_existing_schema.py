"""baseline - pre-existing schema

Revision ID: 66354c5537fc
Revises: 
Create Date: 2026-09-22 16:28:54.862139

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66354c5537fc'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Deliberately empty.

    The `events` and `orders` tables already existed before Alembic was
    introduced — the official postgres image runs `app/seed.sql` (shipped as
    ConfigMap/postgres-seed, mounted at /docker-entrypoint-initdb.d/01-seed.sql)
    once, on an empty data directory.

    This revision is a marker, applied with `alembic stamp head` rather than
    `alembic upgrade`, that says "the live schema equals this point in history".
    Every real migration hangs off it.
    """


def downgrade() -> None:
    """Deliberately empty — see upgrade()."""
