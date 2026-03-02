"""add user disable_mentions flag

Revision ID: add_user_disable_mentions
Revises: add_user_global_streaks
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa


revision = "add_user_disable_mentions"
down_revision = "add_user_global_streaks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("disable_mentions", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "disable_mentions", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "disable_mentions")

