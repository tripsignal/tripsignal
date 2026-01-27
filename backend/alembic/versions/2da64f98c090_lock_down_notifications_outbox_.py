"""lock down notifications_outbox semantics (sent_at + constraints)

Revision ID: 2da64f98c090
Revises: 1f49572a7614
Create Date: 2026-01-27 23:48:39.225812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2da64f98c090'
down_revision: Union[str, None] = '1f49572a7614'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
