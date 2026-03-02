"""add created_at to hotel_links

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-03-01

Adds created_at TIMESTAMP(timezone) to hotel_links table.
Backfills existing rows with the earliest found_at from deals.
"""
from alembic import op
import sqlalchemy as sa


revision = 'k1f2a3b4c5d6'
down_revision = 'j0e1f2a3b4c5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('hotel_links', sa.Column(
        'created_at', sa.DateTime(timezone=True),
        server_default=sa.text('now()'), nullable=True,
    ))
    # Backfill existing rows with the earliest deal found_at for each hotel
    op.execute("""
        UPDATE hotel_links h
        SET created_at = earliest.first_seen
        FROM (
            SELECT hotel_id, MIN(found_at) AS first_seen
            FROM deals
            WHERE hotel_id IS NOT NULL
            GROUP BY hotel_id
        ) earliest
        WHERE h.hotel_id = earliest.hotel_id
    """)


def downgrade() -> None:
    op.drop_column('hotel_links', 'created_at')
