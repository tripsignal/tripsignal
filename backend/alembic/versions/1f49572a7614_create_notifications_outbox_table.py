"""create notifications_outbox table

Revision ID: 1f49572a7614
Revises: 52ad9123d649
Create Date: 2026-01-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1f49572a7614"
down_revision: Union[str, None] = "52ad9123d649"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create notifications_outbox table.
    op.create_table(
        "notifications_outbox",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        sa.Column("last_error", sa.Text(), nullable=True),

        sa.Column("signal_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),

        sa.Column("channel", sa.String(length=20), nullable=False, server_default="log"),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
    )

    op.create_index("ix_notifications_outbox_signal_id", "notifications_outbox", ["signal_id"])
    op.create_index("ix_notifications_outbox_match_id", "notifications_outbox", ["match_id"])
    op.create_index("ix_notifications_outbox_status_next", "notifications_outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_outbox_status_next", table_name="notifications_outbox")
    op.drop_index("ix_notifications_outbox_match_id", table_name="notifications_outbox")
    op.drop_index("ix_notifications_outbox_signal_id", table_name="notifications_outbox")
    op.drop_table("notifications_outbox")
