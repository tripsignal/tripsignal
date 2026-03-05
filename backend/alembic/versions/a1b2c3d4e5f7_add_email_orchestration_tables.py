"""add email orchestration tables and columns

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
Create Date: 2026-02-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'a1b2c3d4e5f7'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── email_log table ──────────────────────────────────────────────────
    op.create_table(
        'email_log',
        sa.Column('id', sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('email_type', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), server_default=sa.text("'transactional'"), nullable=False),
        sa.Column('idempotency_key', sa.Text(), nullable=False),
        sa.Column('to_email', sa.Text(), nullable=False),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('provider_message_id', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default=sa.text("'sent'"), nullable=False),
        sa.Column('suppressed_reason', sa.Text(), nullable=True),
        sa.Column('metadata_json', JSONB(), nullable=True),
        sa.Column('sent_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_email_log_user_id', 'email_log', ['user_id'])
    op.create_index('ix_email_log_email_type', 'email_log', ['email_type'])
    op.create_index('ix_email_log_user_type', 'email_log', ['user_id', 'email_type'])
    op.create_index('ix_email_log_created', 'email_log', ['created_at'])

    # ── stripe_events table ──────────────────────────────────────────────
    op.create_table(
        'stripe_events',
        sa.Column('id', sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column('stripe_event_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('received_at', sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('processed_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('processing_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stripe_event_id'),
    )
    op.create_index('ix_stripe_events_stripe_event_id', 'stripe_events', ['stripe_event_id'])
    op.create_index('ix_stripe_events_event_type', 'stripe_events', ['event_type'])

    # ── New columns on users ─────────────────────────────────────────────
    op.add_column('users', sa.Column(
        'welcome_email_sent_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))
    op.add_column('users', sa.Column(
        'trial_expiring_email_sent_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))
    op.add_column('users', sa.Column(
        'no_signal_email_sent_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))

    # ── New column on signals ────────────────────────────────────────────
    op.add_column('signals', sa.Column(
        'no_match_email_sent_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))

    # ── New columns on deal_matches ──────────────────────────────────────
    op.add_column('deal_matches', sa.Column(
        'notified_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))
    op.add_column('deal_matches', sa.Column(
        'major_drop_alert_sent_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('deal_matches', 'major_drop_alert_sent_at')
    op.drop_column('deal_matches', 'notified_at')
    op.drop_column('signals', 'no_match_email_sent_at')
    op.drop_column('users', 'no_signal_email_sent_at')
    op.drop_column('users', 'trial_expiring_email_sent_at')
    op.drop_column('users', 'welcome_email_sent_at')
    op.drop_table('stripe_events')
    op.drop_table('email_log')
