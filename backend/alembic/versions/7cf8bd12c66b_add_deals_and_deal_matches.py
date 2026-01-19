"""Add deals and deal matches

Revision ID: 7cf8bd12c66b
Revises: e94c8a6eeb16
Create Date: 2026-01-18 23:46:38.305210

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7cf8bd12c66b'
down_revision: Union[str, None] = 'e94c8a6eeb16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create deals table
    op.create_table(
        'deals',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('origin', sa.Text(), nullable=False),
        sa.Column('destination', sa.Text(), nullable=False),
        sa.Column('depart_date', sa.Date(), nullable=False),
        sa.Column('return_date', sa.Date(), nullable=True),
        sa.Column('price_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.Text(), server_default=sa.text("'CAD'"), nullable=False),
        sa.Column('deeplink_url', sa.Text(), nullable=True),
        sa.Column('airline', sa.Text(), nullable=True),
        sa.Column('cabin', sa.Text(), nullable=True),
        sa.Column('stops', sa.Integer(), nullable=True),
        sa.Column('found_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('dedupe_key', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for deals table
    op.create_index('ix_deals_provider', 'deals', ['provider'], unique=False)
    op.create_index('ix_deals_origin', 'deals', ['origin'], unique=False)
    op.create_index('ix_deals_destination', 'deals', ['destination'], unique=False)
    op.create_index('ix_deals_depart_date', 'deals', ['depart_date'], unique=False)
    op.create_index('ix_deals_return_date', 'deals', ['return_date'], unique=False)
    op.create_index('ix_deals_price_cents', 'deals', ['price_cents'], unique=False)
    op.create_index('ix_deals_found_at', 'deals', ['found_at'], unique=False)
    op.create_index('ix_deals_dedupe_key', 'deals', ['dedupe_key'], unique=True)
    
    # Create deal_matches table
    op.create_table(
        'deal_matches',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('signal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('deal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('matched_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['deal_id'], ['deals.id'], ),
        sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('signal_id', 'deal_id', name='uq_deal_matches_signal_deal')
    )
    
    # Create indexes for deal_matches table
    op.create_index('ix_deal_matches_signal_id', 'deal_matches', ['signal_id'], unique=False)
    op.create_index('ix_deal_matches_deal_id', 'deal_matches', ['deal_id'], unique=False)
    op.create_index('ix_deal_matches_matched_at', 'deal_matches', ['matched_at'], unique=False)


def downgrade() -> None:
    # Drop indexes for deal_matches
    op.drop_index('ix_deal_matches_matched_at', table_name='deal_matches')
    op.drop_index('ix_deal_matches_deal_id', table_name='deal_matches')
    op.drop_index('ix_deal_matches_signal_id', table_name='deal_matches')
    
    # Drop deal_matches table
    op.drop_table('deal_matches')
    
    # Drop indexes for deals
    op.drop_index('ix_deals_dedupe_key', table_name='deals')
    op.drop_index('ix_deals_found_at', table_name='deals')
    op.drop_index('ix_deals_price_cents', table_name='deals')
    op.drop_index('ix_deals_return_date', table_name='deals')
    op.drop_index('ix_deals_depart_date', table_name='deals')
    op.drop_index('ix_deals_destination', table_name='deals')
    op.drop_index('ix_deals_origin', table_name='deals')
    op.drop_index('ix_deals_provider', table_name='deals')
    
    # Drop deals table
    op.drop_table('deals')
