from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.handlers.help_content import global_visibility_text, notifications_text
from app.bot.keyboards.help import help_global_visibility_kb, help_notifications_kb
from app.bot.keyboards.q1 import q1_keyboard
from app.db.session import db_session
from app.services.q1_service import render_q1, render_q1_private
from app.services.q2_q3_service import ensure_q2_q3_exist, should_show_q2_q3_button
from app.services.repo_service import get_or_create_session, get_session_message_id
from app.services.time_service import get_session_window, now_in_tz

logger = logging.getLogger(__name__)


async def render_notifications_panel(
    cb: CallbackQuery,
    *,
    owner_id: int,
    chat,
    actor_disable_mentions: bool,
) -> None:
    if cb.message is None:
        return
    await cb.message.edit_text(
        notifications_text(
            enabled=bool(chat.notifications_enabled),
            post_time_text=chat.post_time.strftime("%H:%M"),
            late_reminder_enabled=bool(chat.late_reminder_enabled),
            q2_q3_enabled=bool(chat.q2_q3_enabled),
            disable_mentions=actor_disable_mentions,
        ),
        parse_mode="HTML",
        reply_markup=help_notifications_kb(
            owner_id,
            current_hour=chat.post_time.hour,
            notifications_enabled=bool(chat.notifications_enabled),
            late_reminder_enabled=bool(chat.late_reminder_enabled),
            q2_q3_enabled=bool(chat.q2_q3_enabled),
            disable_mentions=actor_disable_mentions,
        ),
    )


async def render_global_visibility_panel(
    cb: CallbackQuery,
    *,
    owner_id: int,
    chat,
) -> None:
    if cb.message is None:
        return
    await cb.message.edit_text(
        global_visibility_text(bool(chat.show_in_global)),
        parse_mode="HTML",
        reply_markup=help_global_visibility_kb(owner_id, bool(chat.show_in_global)),
    )


async def refresh_q1_after_settings_change(
    cb: CallbackQuery,
    *,
    db,
    chat,
    chat_id: int,
    actor_id: int,
    is_private_chat: bool,
) -> None:
    window = get_session_window(chat.timezone)
    if window.is_blocked_window:
        return

    sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)
    q1_id = get_session_message_id(db, sess.session_id, "Q1")
    if q1_id and sess.status != "closed":
        text = (
            render_q1_private(
                db,
                chat_id=chat_id,
                session_id=sess.session_id,
                user_id=actor_id,
                session_date=window.session_date,
            )
            if is_private_chat
            else render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
        )
        has_any_members = True if is_private_chat else ("Участники:" in text)
        try:
            await cb.bot.edit_message_text(
                chat_id=chat_id,
                message_id=q1_id,
                text=text,
                reply_markup=q1_keyboard(
                    has_any_members,
                    show_remind=now_in_tz(chat.timezone).time().hour < 22,
                    show_q2_q3_button=should_show_q2_q3_button(
                        db,
                        chat_q2_q3_enabled=bool(chat.q2_q3_enabled),
                        session_id=sess.session_id,
                        is_private_chat=is_private_chat,
                    ),
                ),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.exception("Failed to edit Q1 after help-side change: %s", exc)

    if bool(chat.q2_q3_enabled) and not is_private_chat:
        try:
            await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)
        except Exception:
            logger.exception("Failed to refresh Q2/Q3 after help-side change")
