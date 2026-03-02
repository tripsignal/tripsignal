"""stub for deleted early migration (pre-notifications outbox)

Revision ID: 52ad9123d649
Revises: e94c8a6eeb16
Create Date: 2026-01-22

This file was recreated as a no-op stub. The original migration was
deleted but is referenced by 1f49572a7614. The schema changes it
contained are already present in the database.
"""
from alembic import op
import sqlalchemy as sa


revision = '52ad9123d649'
down_revision = 'e94c8a6eeb16'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
