"""add email column to events

Revision ID: 5fcf405ffc77
Revises: f654912bc7e1
Create Date: 2026-10-04 00:10:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "5fcf405ffc77"
down_revision = "f654912bc7e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("events", "email")
