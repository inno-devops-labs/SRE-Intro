"""Add a nullable email column to events.

Revision ID: 9a0000000002
Revises: 9a0000000001
"""
from alembic import op
import sqlalchemy as sa

revision = "9a0000000002"
down_revision = "9a0000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No table rewrite, but ALTER TABLE still needs an exclusive lock.
    # Fail promptly rather than waiting indefinitely under live traffic.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("events", "email")
