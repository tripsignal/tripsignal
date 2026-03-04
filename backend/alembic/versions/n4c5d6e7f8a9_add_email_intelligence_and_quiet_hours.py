"""add email intelligence and quiet hours columns to users

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-03-04

Adds to users table:
- email_mode: active/passive/dormant user classification
- last_email_opened_at, last_email_clicked_at: engagement tracking
- alert_threshold: text (any/drops/records)
- email_send_hour: preferred send hour
- timezone: user timezone
- quiet_hours_enabled, quiet_hours_start, quiet_hours_end: quiet hours

Also adds:
- signal_intel_cache table (if not exists)
- run_id + no_match_email_sent_at to signals
- suppressed_reason to email_log
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = 'n4c5d6e7f8a9'
down_revision = 'm3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Users: email intelligence columns ──
    op.add_column('users', sa.Column(
        'email_mode', sa.Text(), nullable=False, server_default='active',
    ))
    op.add_column('users', sa.Column(
        'last_email_opened_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))
    op.add_column('users', sa.Column(
        'last_email_clicked_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))
    op.add_column('users', sa.Column(
        'alert_threshold', sa.Text(), nullable=False, server_default='any',
    ))
    op.add_column('users', sa.Column(
        'email_send_hour', sa.Integer(), nullable=True,
    ))
    op.add_column('users', sa.Column(
        'timezone', sa.Text(), nullable=True, server_default='America/Toronto',
    ))

    # ── Users: quiet hours columns ──
    op.add_column('users', sa.Column(
        'quiet_hours_enabled', sa.Boolean(), nullable=False, server_default='false',
    ))
    op.add_column('users', sa.Column(
        'quiet_hours_start', sa.Text(), nullable=True, server_default='21:00',
    ))
    op.add_column('users', sa.Column(
        'quiet_hours_end', sa.Text(), nullable=True, server_default='08:00',
    ))

    # ── Signals: no_match tracking ──
    op.add_column('signals', sa.Column(
        'no_match_email_sent_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))

    # ── Deal matches: run_id ──
    op.add_column('deal_matches', sa.Column(
        'run_id', sa.Text(), nullable=True,
    ))

    # ── Email log: suppressed_reason ──
    op.add_column('email_log', sa.Column(
        'suppressed_reason', sa.Text(), nullable=True,
    ))

    # ── Signal intel cache table ──
    op.create_table(
        'signal_intel_cache',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('signal_id', UUID(as_uuid=True), sa.ForeignKey('signals.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('total_matches', sa.Integer(), nullable=True),
        sa.Column('min_price_ever_cents', sa.Integer(), nullable=True),
        sa.Column('current_deal_percentile', sa.Float(), nullable=True),
        sa.Column('trend_direction', sa.Text(), nullable=True),
        sa.Column('trend_consecutive_weeks', sa.Integer(), nullable=True),
        sa.Column('avg_price_per_night_cents', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
    )


def downgrade() -> None:
    op.drop_table('signal_intel_cache')
    op.drop_column('email_log', 'suppressed_reason')
    op.drop_column('deal_matches', 'run_id')
    op.drop_column('signals', 'no_match_email_sent_at')
    op.drop_column('users', 'quiet_hours_end')
    op.drop_column('users', 'quiet_hours_start')
    op.drop_column('users', 'quiet_hours_enabled')
    op.drop_column('users', 'timezone')
    op.drop_column('users', 'email_send_hour')
    op.drop_column('users', 'alert_threshold')
    op.drop_column('users', 'last_email_clicked_at')
    op.drop_column('users', 'last_email_opened_at')
    op.drop_column('users', 'email_mode')
