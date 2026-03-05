"""add advanced intel insights columns and route_intel_cache table

Revision ID: q7f8a9b0c1d2
Revises: p6e7f8a9b0c1
Create Date: 2026-03-04

Adds to signal_intel_cache:
- trend_velocity, trend_last_week_delta_cents, trend_prev_week_delta_cents (momentum)
- trend_inflection, inflection_pct_change (early warning)
- star_price_anomaly_pct, hero_star_rating (anomaly detection)
- floor_proximity_pct (price floor)
- value_score (composite 0-100)

Creates route_intel_cache table for route-level insights:
- departure window heatmap
- destination price index
- booking countdown pressure
"""
from alembic import op
import sqlalchemy as sa


revision = 'q7f8a9b0c1d2'
down_revision = 'p6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── signal_intel_cache: momentum velocity ──
    op.add_column('signal_intel_cache', sa.Column(
        'trend_velocity', sa.Text(), nullable=True,
    ))
    op.add_column('signal_intel_cache', sa.Column(
        'trend_last_week_delta_cents', sa.Integer(), nullable=True,
    ))
    op.add_column('signal_intel_cache', sa.Column(
        'trend_prev_week_delta_cents', sa.Integer(), nullable=True,
    ))

    # ── signal_intel_cache: trend inflection ──
    op.add_column('signal_intel_cache', sa.Column(
        'trend_inflection', sa.Boolean(), nullable=True, server_default='false',
    ))
    op.add_column('signal_intel_cache', sa.Column(
        'inflection_pct_change', sa.Float(), nullable=True,
    ))

    # ── signal_intel_cache: star anomaly ──
    op.add_column('signal_intel_cache', sa.Column(
        'star_price_anomaly_pct', sa.Float(), nullable=True,
    ))
    op.add_column('signal_intel_cache', sa.Column(
        'hero_star_rating', sa.Float(), nullable=True,
    ))

    # ── signal_intel_cache: floor proximity ──
    op.add_column('signal_intel_cache', sa.Column(
        'floor_proximity_pct', sa.Float(), nullable=True,
    ))

    # ── signal_intel_cache: value score ──
    op.add_column('signal_intel_cache', sa.Column(
        'value_score', sa.Integer(), nullable=True,
    ))

    # ── route_intel_cache table ──
    op.create_table(
        'route_intel_cache',
        sa.Column('origin', sa.Text(), nullable=False),
        sa.Column('destination_region', sa.Text(), nullable=False),
        sa.Column('cheapest_depart_week', sa.Date(), nullable=True),
        sa.Column('cheapest_week_avg_cents', sa.Integer(), nullable=True),
        sa.Column('priciest_depart_week', sa.Date(), nullable=True),
        sa.Column('priciest_week_avg_cents', sa.Integer(), nullable=True),
        sa.Column('current_week_avg_cents', sa.Integer(), nullable=True),
        sa.Column('prev_week_avg_cents', sa.Integer(), nullable=True),
        sa.Column('week_over_week_pct', sa.Float(), nullable=True),
        sa.Column('avg_price_4plus_weeks_cents', sa.Integer(), nullable=True),
        sa.Column('avg_price_2to4_weeks_cents', sa.Integer(), nullable=True),
        sa.Column('avg_price_under_2_weeks_cents', sa.Integer(), nullable=True),
        sa.Column('late_booking_premium_pct', sa.Float(), nullable=True),
        sa.Column('total_deals_analyzed', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('cache_refreshed_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('origin', 'destination_region'),
    )


def downgrade() -> None:
    op.drop_table('route_intel_cache')
    op.drop_column('signal_intel_cache', 'value_score')
    op.drop_column('signal_intel_cache', 'floor_proximity_pct')
    op.drop_column('signal_intel_cache', 'hero_star_rating')
    op.drop_column('signal_intel_cache', 'star_price_anomaly_pct')
    op.drop_column('signal_intel_cache', 'inflection_pct_change')
    op.drop_column('signal_intel_cache', 'trend_inflection')
    op.drop_column('signal_intel_cache', 'trend_prev_week_delta_cents')
    op.drop_column('signal_intel_cache', 'trend_last_week_delta_cents')
    op.drop_column('signal_intel_cache', 'trend_velocity')
