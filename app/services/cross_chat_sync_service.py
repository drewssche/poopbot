from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.keyboards.q1 import q1_keyboard
from app.db.models import Chat, ChatMember, Session as DaySession, SessionUserState
from app.services.poop_event_service import list_events, reconcile_events_count
from app.services.q1_service import render_q1, render_q1_private, should_show_restore_streak_button
from app.services.q2_q3_service import ensure_q2_q3_exist, should_show_q2_q3_button
from app.services.repo_service import get_or_create_session, get_or_create_session_user_state, get_session_message_id
from app.services.time_service import get_session_window

logger = logging.getLogger(__name__)


def sync_user_state_across_member_chats(
    db: Session,
    *,
    source_chat_id: int,
    source_session_id: int,
    user_id: int,
) -> list[tuple[int, int]]:
    source_state = db.get(SessionUserState, {"session_id": source_session_id, "user_id": user_id})
    source_events = list_events(db, source_session_id, user_id)
    source_events_by_n = {int(e.event_n): e for e in source_events}

    source_poops = int(source_state.poops_n) if source_state else 0
    source_bristol = source_state.bristol if source_state else None
    source_feeling = source_state.feeling if source_state else None
    source_achievement = source_state.achievement_text if source_state else None

    member_chat_ids = [
        int(cid)
        for cid in db.scalars(select(ChatMember.chat_id).where(ChatMember.user_id == user_id)).all()
    ]
    touched: list[tuple[int, int]] = []

    for target_chat_id in member_chat_ids:
        if target_chat_id == source_chat_id:
            continue

        chat = db.get(Chat, target_chat_id)
        if chat is None or not bool(chat.is_enabled):
            continue

        target_date = get_session_window(chat.timezone).session_date
        target_sess = get_or_create_session(db, chat_id=target_chat_id, session_date=target_date)
        st = get_or_create_session_user_state(db, target_sess.session_id, user_id)

        st.poops_n = source_poops
        if source_poops <= 0:
            st.achievement_text = None
            st.bristol = None
            st.feeling = None
        else:
            st.achievement_text = source_achievement
            st.bristol = source_bristol
            st.feeling = source_feeling

        reconcile_events_count(
            db,
            session_id=target_sess.session_id,
            user_id=user_id,
            poops_n=source_poops,
            origin_chat_id=source_chat_id,
        )
        target_events = list_events(db, target_sess.session_id, user_id)
        target_events_by_n = {int(e.event_n): e for e in target_events}

        for n in range(1, source_poops + 1):
            src = source_events_by_n.get(n)
            dst = target_events_by_n.get(n)
            if src is None or dst is None:
                continue
            dst.bristol = src.bristol
            dst.feeling = src.feeling
            dst.origin_chat_id = source_chat_id

        touched.append((target_chat_id, int(target_sess.session_id)))

    return touched


async def refresh_synced_chats_views(bot: Bot, db: Session, touched_sessions: list[tuple[int, int]]) -> None:
    for chat_id, session_id in touched_sessions:
        sess = db.get(DaySession, session_id)
        if sess is None or sess.status == "closed":
            continue

        q1_id = get_session_message_id(db, session_id, "Q1")
        if q1_id:
            chat = db.get(Chat, chat_id)
            try:
                text = (
                    render_q1_private(db, chat_id=chat_id, session_id=session_id, user_id=chat_id, session_date=sess.session_date)
                    if chat_id > 0
                    else render_q1(db, chat_id=chat_id, session_id=session_id, session_date=sess.session_date)
                )
                has_any_members = True if chat_id > 0 else ("Участники:" in text)
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=q1_id,
                    text=text,
                    reply_markup=q1_keyboard(
                        has_any_members,
                        show_restore_streak_button=should_show_restore_streak_button(
                            db,
                            chat_id=chat_id,
                            session_date=sess.session_date,
                            viewer_user_id=chat_id if chat_id > 0 else None,
                            is_private_chat=chat_id > 0,
                        ),
                        show_q2_q3_button=should_show_q2_q3_button(
                            db,
                            chat_q2_q3_enabled=bool(chat.q2_q3_enabled) if chat is not None else False,
                            session_id=session_id,
                            is_private_chat=chat_id > 0,
                        ),
                    ),
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.warning("Q1 sync refresh skipped chat_id=%s session_id=%s err=%s", chat_id, session_id, e)
            except Exception as e:
                logger.warning("Q1 sync refresh failed chat_id=%s session_id=%s err=%s", chat_id, session_id, e)

        chat = db.get(Chat, chat_id)
        if chat is not None and chat_id < 0 and bool(chat.q2_q3_enabled):
            try:
                await ensure_q2_q3_exist(bot, db, chat_id, session_id)
            except Exception as e:
                logger.warning("Q2/Q3 sync refresh failed chat_id=%s session_id=%s err=%s", chat_id, session_id, e)
