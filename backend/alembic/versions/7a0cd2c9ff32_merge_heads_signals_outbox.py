"""merge heads (signals + outbox)

Revision ID: 7a0cd2c9ff32
Revises: 7320e9169655, 94fe01937224
Create Date: 2026-01-30 05:54:53.183446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a0cd2c9ff32'
down_revision: Union[str, None] = ('7320e9169655', '94fe01937224')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
