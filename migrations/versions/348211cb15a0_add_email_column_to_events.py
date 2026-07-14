"""Add the nullable events.email column from Lab 9."""

import sqlalchemy as sa
from alembic import op

revision = "348211cb15a0"
down_revision = "4c99d2b4cc90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("email", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("events", "email")
