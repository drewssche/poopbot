"""add help message tracking to chats

Revision ID: add_help_message_tracking
Revises: 388f1a7dda8d
Create Date: 2026-02-10

"""
from alembic import op
import sqlalchemy as sa

revision = "add_help_message_tracking"
down_revision = "388f1a7dda8d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("help_message_id", sa.BigInteger(), nullable=True))
    op.add_column("chats", sa.Column("help_owner_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "help_owner_id")
    op.drop_column("chats", "help_message_id")
