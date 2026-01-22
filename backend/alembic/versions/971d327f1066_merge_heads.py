"""merge heads

Revision ID: 971d327f1066
Revises: 8a29364bcc68
Create Date: 2026-01-22 02:13:14.014664

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '971d327f1066'
down_revision: Union[str, None] = '8a29364bcc68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
