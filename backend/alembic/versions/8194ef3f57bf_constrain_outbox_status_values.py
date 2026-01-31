"""Constrain notifications_outbox.status to allowed values.

Revision ID: 8194ef3f57bf
Revises: 7a0cd2c9ff32
Create Date: <AUTO>
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "8194ef3f57bf"
down_revision = "7a0cd2c9ff32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Safety: if any old rows exist with legacy status, normalize them.
    op.execute(
        """
        UPDATE notifications_outbox
        SET status = 'pending'
        WHERE status = 'queued';
        """
    )

    # 2) Add a DB constraint so only allowed statuses can exist.
    #    This prevents regressions where code accidentally writes "queued" again.
    op.execute(
        """
        ALTER TABLE notifications_outbox
        ADD CONSTRAINT ck_notifications_outbox_status_valid
        CHECK (status IN ('pending', 'sending', 'sent', 'dead'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE notifications_outbox
        DROP CONSTRAINT IF EXISTS ck_notifications_outbox_status_valid;
        """
    )
