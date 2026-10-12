"""add email column to events

Revision ID: 7e0f8d34fcbe
Revises: 8f41e8ca0ca6
"""
from alembic import op
import sqlalchemy as sa

revision = "7e0f8d34fcbe"
down_revision = "8f41e8ca0ca6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("email", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "email")
