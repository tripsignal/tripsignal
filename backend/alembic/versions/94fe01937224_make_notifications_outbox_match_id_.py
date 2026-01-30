"""make notifications_outbox match_id nullable

Revision ID: 94fe01937224
Revises: 2da64f98c090
Create Date: 2026-01-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "94fe01937224"
down_revision: Union[str, None] = "2da64f98c090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Allow admin/test emails to omit match_id
    op.alter_column(
        "notifications_outbox",
        "match_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    # Downgrade would fail if NULLs exist, so delete NULL rows first
    op.execute("DELETE FROM notifications_outbox WHERE match_id IS NULL")

    op.alter_column(
        "notifications_outbox",
        "match_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
