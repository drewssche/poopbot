"""add chat visibility in global stats

Revision ID: add_chat_show_in_global
Revises: add_poop_events
Create Date: 2026-02-13

"""
from alembic import op
import sqlalchemy as sa

revision = "add_chat_show_in_global"
down_revision = "add_poop_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("show_in_global", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.alter_column("chats", "show_in_global", server_default=None)


def downgrade() -> None:
    op.drop_column("chats", "show_in_global")
