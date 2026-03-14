"""add composite index on deals (hotel_name, origin, depart_date, is_active)

Revision ID: a1b2c3d4e5f6
Revises: a1h1b2c3d4e5
Create Date: 2026-03-14

Covers the three smart-insight queries (_get_nearby_airport_saving,
_get_date_shift_saving, _get_budget_alternatives) which all filter
on hotel_name + origin/destination + depart_date + is_active.
"""
from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "a1h1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_deals_hotel_name_origin_depart_is_active "
        "ON deals (hotel_name, origin, depart_date, is_active)"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_deals_hotel_name_origin_depart_is_active"
    )
