"""add chat late reminder and q2_q3 toggles

Revision ID: add_chat_late_q2q3_toggles
Revises: add_chat_notifications_enabled
Create Date: 2026-02-16
"""

from alembic import op
import sqlalchemy as sa


revision = "add_chat_late_q2q3_toggles"
down_revision = "add_chat_notifications_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("late_reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "chats",
        sa.Column("q2_q3_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("chats", "late_reminder_enabled", server_default=None)
    op.alter_column("chats", "q2_q3_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("chats", "q2_q3_enabled")
    op.drop_column("chats", "late_reminder_enabled")
