"""add unsubscribe_reason column to users

Revision ID: b9c0d1e2f3a4
Revises: z7p8q9r0s1t2
Create Date: 2026-03-12

Stores the user's self-reported reason for unsubscribing from deal emails.
"""
from alembic import op
import sqlalchemy as sa


revision = "b9c0d1e2f3a4"
down_revision = "a8q9r0s1t2u3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("unsubscribe_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "unsubscribe_reason")
