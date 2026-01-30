"""add user_id to signals

Revision ID: 7320e9169655
Revises: cf2b18c43f3b
Create Date: 2026-01-30

This migration intentionally ONLY adds signals.user_id (FK to users.id) + index.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7320e9169655"
down_revision: Union[str, None] = "cf2b18c43f3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Add the column as nullable first (so it doesn't fail on existing rows)
    op.add_column(
        "signals",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 2) Backfill existing rows to your DEV fallback user id (the one you mentioned)
    op.execute(
        "UPDATE signals SET user_id = '9b2bb98a-0c15-4726-9c20-de3b81e5172f' WHERE user_id IS NULL"
    )

    # 3) Make it NOT NULL going forward
    op.alter_column("signals", "user_id", nullable=False)

    # 4) Add FK + index
    op.create_foreign_key(
        "signals_user_id_fkey",
        "signals",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_signals_user_id", "signals", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_signals_user_id", table_name="signals")
    op.drop_constraint("signals_user_id_fkey", "signals", type_="foreignkey")
    op.drop_column("signals", "user_id")
