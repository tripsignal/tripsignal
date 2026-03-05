"""add notification_delivery_frequency to users

Revision ID: p6e7f8a9b0c1
Revises: o5d6e7f8a9b0
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'p6e7f8a9b0c1'
down_revision = 'o5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new frequency column
    op.add_column(
        'users',
        sa.Column(
            'notification_delivery_frequency',
            sa.Text(),
            nullable=False,
            server_default=sa.text("'all'"),
        ),
    )

    # Migrate existing data
    op.execute(
        "UPDATE users SET notification_delivery_frequency = 'all' "
        "WHERE notification_delivery_speed = 'immediate'"
    )
    op.execute(
        "UPDATE users SET notification_delivery_frequency = 'morning' "
        "WHERE notification_delivery_speed = 'daily'"
    )

    # Drop unused email_send_hour column
    op.drop_column('users', 'email_send_hour')


def downgrade() -> None:
    op.add_column(
        'users',
        sa.Column('email_send_hour', sa.Integer(), nullable=True),
    )
    op.drop_column('users', 'notification_delivery_frequency')
