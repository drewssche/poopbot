"""add performance indexes for critical queries

Revision ID: add_performance_indexes
Revises: add_app_settings
Create Date: 2026-03-24

"""
from alembic import op
import sqlalchemy as sa

revision = "add_performance_indexes"
down_revision = "add_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Индекс для scheduler_service._recalculate_streaks_from_history
    # Используется в ежедневном 00:06 пересчёте стриков
    op.create_index(
        "ix_sessions_chat_date",
        "sessions",
        ["chat_id", "session_date"]
    )
    
    # Индекс для stats_streaks.compute_chat_user_streaks_live
    # Критичен для /stats в чатах с большим количеством участников
    op.create_index(
        "ix_poop_events_origin_session_user",
        "poop_events",
        ["origin_chat_id", "session_id", "user_id"]
    )
    
    # Индекс для cross_chat_sync_service и q1_service.render_q1
    # Ускоряет выборку состояний пользователей по сессии
    op.create_index(
        "ix_session_user_state_session_poops",
        "session_user_state",
        ["session_id", "poops_n", "user_id"]
    )
    
    # Индекс для stats_service и scheduler_reports — поиск по chat_id
    op.create_index(
        "ix_chat_members_user",
        "chat_members",
        ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_members_user", table_name="chat_members")
    op.drop_index("ix_session_user_state_session_poops", table_name="session_user_state")
    op.drop_index("ix_poop_events_origin_session_user", table_name="poop_events")
    op.drop_index("ix_sessions_chat_date", table_name="sessions")
