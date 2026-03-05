"""add system_config table

Revision ID: r8g9h0i1j2k3
Revises: q7f8a9b0c1d2
Create Date: 2026-03-05

Formalises the system_config table used by /api/system/next-scan.
Uses IF NOT EXISTS guard since the table may already exist from raw SQL.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "r8g9h0i1j2k3"
down_revision = "q7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "system_config" not in inspector.get_table_names():
        op.create_table(
            "system_config",
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("key"),
        )


def downgrade() -> None:
    op.drop_table("system_config")
