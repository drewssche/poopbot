"""add origin_chat_id to poop_events

Revision ID: add_poop_event_origin_chat_id
Revises: add_chat_late_q2q3_toggles
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa

revision = "add_poop_event_origin_chat_id"
down_revision = "add_chat_late_q2q3_toggles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("poop_events", sa.Column("origin_chat_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE poop_events pe
        SET origin_chat_id = s.chat_id
        FROM sessions s
        WHERE s.session_id = pe.session_id
        """
    )
    op.alter_column("poop_events", "origin_chat_id", nullable=False)
    op.create_index("ix_poop_events_origin_chat", "poop_events", ["origin_chat_id"])


def downgrade() -> None:
    op.drop_index("ix_poop_events_origin_chat", table_name="poop_events")
    op.drop_column("poop_events", "origin_chat_id")
