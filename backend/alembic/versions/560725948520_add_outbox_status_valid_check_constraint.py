"""Add notifications_outbox.status allowed-values check constraint.

Revision ID: 560725948520
Revises: 8194ef3f57bf
Create Date: 2026-01-31
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "560725948520"
down_revision = "8194ef3f57bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safety: normalize any legacy value if it exists (should be none, but harmless)
    op.execute(
        """
        UPDATE notifications_outbox
        SET status = 'pending'
        WHERE status = 'queued';
        """
    )

    # Enforce allowed status values at the DB level
    op.create_check_constraint(
        "outbox_status_valid_values",
        "notifications_outbox",
        "status IN ('pending','sending','sent','dead')",
    )


def downgrade() -> None:
    op.drop_constraint("outbox_status_valid_values", "notifications_outbox", type_="check")
