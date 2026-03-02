"""merge all heads

Revision ID: h8c9d0e1f2a3
Revises: a1b2c3d4e5f6, g7b8c9d0e1f2, 0ce68cc5557c, 2da64f98c090
Create Date: 2026-03-01

"""
from alembic import op
import sqlalchemy as sa


revision = 'h8c9d0e1f2a3'
down_revision = ('a1b2c3d4e5f6', 'g7b8c9d0e1f2', '0ce68cc5557c', '2da64f98c090')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
