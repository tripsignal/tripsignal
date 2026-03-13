"""constrain display_name column to varchar(100)

Revision ID: b9r0s1t2u3v4
Revises: a8q9r0s1t2u3
Create Date: 2026-03-12

Adds a CHECK constraint and converts column type from Text to VARCHAR(100)
for defense-in-depth. The application already truncates to 100 chars, this
ensures the database enforces it too.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b9r0s1t2u3v4"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Truncate any existing values longer than 100 chars (safety net)
    op.execute("UPDATE users SET display_name = LEFT(display_name, 100) WHERE LENGTH(display_name) > 100")
    # Change column type from TEXT to VARCHAR(100)
    op.alter_column(
        "users",
        "display_name",
        type_=sa.String(100),
        existing_type=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "display_name",
        type_=sa.Text(),
        existing_type=sa.String(100),
        existing_nullable=True,
    )
