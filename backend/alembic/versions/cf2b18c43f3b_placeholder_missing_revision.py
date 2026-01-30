"""placeholder for missing revision in DB

Revision ID: cf2b18c43f3b
Revises: e94c8a6eeb16
Create Date: 2026-01-30

NOTE:
The database alembic_version points to cf2b18c43f3b, but the migration file
was missing from the repo. This no-op placeholder restores Alembic's revision
graph so we can continue making migrations safely.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cf2b18c43f3b"
down_revision: Union[str, None] = "e94c8a6eeb16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
