"""add poop_events

Revision ID: add_poop_events
Revises: add_command_messages
Create Date: 2026-02-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "add_poop_events"
down_revision = "add_command_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "poop_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_n", sa.Integer(), nullable=False),
        sa.Column("bristol", sa.Integer(), nullable=True),
        sa.Column(
            "feeling",
            postgresql.ENUM("great", "ok", "bad", name="feeling_kind", create_type=False),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "user_id", "event_n", name="uq_poop_event_per_user"),
    )
    op.create_index("ix_poop_events_session_user", "poop_events", ["session_id", "user_id"])
    op.create_index("ix_poop_events_session_user_n", "poop_events", ["session_id", "user_id", "event_n"])


def downgrade() -> None:
    op.drop_index("ix_poop_events_session_user_n", table_name="poop_events")
    op.drop_index("ix_poop_events_session_user", table_name="poop_events")
    op.drop_table("poop_events")
