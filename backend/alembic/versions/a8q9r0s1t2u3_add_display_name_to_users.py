"""add display_name and name_prompt_dismissed to users

Revision ID: a8q9r0s1t2u3
Revises: z7p8q9r0s1t2
Create Date: 2026-03-12

Adds two columns to support personalised greetings:
- display_name: what the user wants to be called
- name_prompt_dismissed: whether they opted out of the name prompt
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a8q9r0s1t2u3"
down_revision = "z7p8q9r0s1t2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "name_prompt_dismissed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "name_prompt_dismissed")
    op.drop_column("users", "display_name")
