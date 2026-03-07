"""Add email_queue table for rate-limited delivery.

Revision ID: w3l4m5n6o7p8
Revises: v2k3l4m5n6o7
Create Date: 2026-03-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "w3l4m5n6o7p8"
down_revision = "v2k3l4m5n6o7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_queue",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default=sa.text("2")),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("email_log_id", UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("email_type", sa.Text(), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Index for queue drain: status + priority + created_at ordering
    op.create_index("ix_email_queue_drain", "email_queue", ["status", "priority", "created_at"])
    # Index for retry lookup
    op.create_index("ix_email_queue_retry", "email_queue", ["status", "next_retry_at"])
    # Index for email_log linkage
    op.create_index("ix_email_queue_log_id", "email_queue", ["email_log_id"])


def downgrade() -> None:
    op.drop_index("ix_email_queue_log_id")
    op.drop_index("ix_email_queue_retry")
    op.drop_index("ix_email_queue_drain")
    op.drop_table("email_queue")
