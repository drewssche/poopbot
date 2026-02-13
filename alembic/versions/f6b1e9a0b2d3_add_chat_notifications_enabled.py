"""add chat notifications_enabled

Revision ID: add_chat_notifications_enabled
Revises: add_chat_show_in_global
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa


revision = "add_chat_notifications_enabled"
down_revision = "add_chat_show_in_global"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("chats", "notifications_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("chats", "notifications_enabled")
