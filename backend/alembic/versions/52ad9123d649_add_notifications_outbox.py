"""add notifications_outbox (missing revision placeholder)

Revision ID: 52ad9123d649
Revises: 0ce68cc5557c
Create Date: 2026-01-24

NOTE:
This file was missing but is referenced by later migrations.
It is intentionally a no-op placeholder to restore Alembic's revision chain.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "52ad9123d649"
down_revision: Union[str, None] = "0ce68cc5557c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
