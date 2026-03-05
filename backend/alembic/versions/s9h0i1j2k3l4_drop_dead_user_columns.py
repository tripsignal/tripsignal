"""drop dead user columns notification_delivery_speed and alert_threshold

Revision ID: s9h0i1j2k3l4
Revises: r8g9h0i1j2k3
Create Date: 2026-03-05

Removes two columns that are never read by business logic:
- notification_delivery_speed: superseded by notification_delivery_frequency
- alert_threshold: added but never wired into any filtering
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "s9h0i1j2k3l4"
down_revision = "r8g9h0i1j2k3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "notification_delivery_speed")
    op.drop_column("users", "alert_threshold")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "notification_delivery_speed",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'immediate'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "alert_threshold",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'any'"),
        ),
    )
