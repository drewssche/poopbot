"""add command_messages

Revision ID: add_command_messages
Revises: add_help_message_tracking
Create Date: 2026-02-10

"""
from alembic import op
import sqlalchemy as sa

revision = "add_command_messages"
down_revision = "add_help_message_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "command_messages",
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("chat_id", "user_id", "command", "session_date"),
    )


def downgrade() -> None:
    op.drop_table("command_messages")
