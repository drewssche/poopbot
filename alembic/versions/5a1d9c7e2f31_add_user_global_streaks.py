"""add user_global_streaks

Revision ID: add_user_global_streaks
Revises: add_poop_event_origin_chat_id
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa

revision = "add_user_global_streaks"
down_revision = "add_poop_event_origin_chat_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_global_streaks",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_poop_date", sa.Date(), nullable=True),
    )
    op.alter_column("user_global_streaks", "current_streak", server_default=None)


def downgrade() -> None:
    op.drop_table("user_global_streaks")
