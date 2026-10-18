"""baseline - pre-existing schema

Revision ID: 9a0001
Revises:
Create Date: 2026-10-04 19:10:00+00:00
"""

from typing import Sequence, Union

revision: str = "9a0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
