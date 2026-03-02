"""stub for deleted early migration (pre-plan/stripe cols)

Revision ID: 61733afcd940
Revises: e94c8a6eeb16
Create Date: 2026-02-15

This file was recreated as a no-op stub. The original migration was
deleted but is referenced by b2c3d4e5f6a7. The schema changes it
contained are already present in the database.
"""
from alembic import op
import sqlalchemy as sa


revision = '61733afcd940'
down_revision = 'e94c8a6eeb16'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
