from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.db.models import CommandMessage, Session as DaySession, SessionMessage, SessionUserState

from app.bot.keyboards.q1 import q1_keyboard

from app.services.repo_service import (
    upsert_chat,
    upsert_user,
    ensure_chat_member,
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
)
from app.services.time_service import get_session_window, now_in_tz
from app.services.rate_limit_service import check_rate_limit
from app.services.q1_service import render_q1, apply_plus, apply_minus, toggle_remind
from app.services.q2_q3_service import ensure_q2_q3_exist
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.services.reminder_service import REMINDER22_COMMAND, build_reminder_22_text, mark_reminder_ack
from app.services.poop_event_service import reconcile_events_count
from app.bot.keyboards.reminder import reminder_keyboard

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None



def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


@router.callback_query(F.data.in_({"q1:plus", "q1:minus", "q1:remind", "q1:plus_reminder"}))
async def q1_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user

    try:
        with db_session(_session_factory) as db:
            chat = upsert_chat(db, chat_id=chat_id)
            window = get_session_window(chat.timezone)

            if window.is_blocked_window:
                await cb.answer("Новая сессия начнётся в 00:05", show_alert=False)
                return

            if not check_rate_limit(db, chat_id=chat_id, user_id=user.id, scope="Q1", cooldown_seconds=2):
                if cb.data in ("q1:plus", "q1:minus"):
                    await cb.answer("Так быстро не какают", show_alert=False)
                else:
                    await cb.answer("Не так быстро, здоровяк", show_alert=False)
                return

            upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
            db.flush()
            current_sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

            sess = db.scalar(
                select(DaySession)
                .join(SessionMessage, SessionMessage.session_id == DaySession.session_id)
                .where(
                    DaySession.chat_id == chat_id,
                    SessionMessage.kind == "Q1",
                    SessionMessage.message_id == cb.message.message_id,
                )
            )
            if sess is None:
                reminder_row = db.scalar(
                    select(CommandMessage).where(
                        CommandMessage.chat_id == chat_id,
                        CommandMessage.command == REMINDER22_COMMAND,
                        CommandMessage.message_id == cb.message.message_id,
                    )
                )
                if reminder_row is not None:
                    sess = get_or_create_session(db, chat_id=chat_id, session_date=reminder_row.session_date)
            if sess is None:
                if cb.data == "q1:plus_reminder":
                    sess = current_sess
                else:
                    await cb.answer("Неактуально", show_alert=False)
                    return

            if sess.status == "closed":
                await cb.answer("Сессия закрыта", show_alert=False)
                return

            # Для reminder-кнопки допускаем fallback на текущую сессию, если mapping сообщения потерян.
            q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
            reminder_msg_id = get_command_message_id(db, chat_id, 0, REMINDER22_COMMAND, sess.session_date)
            if cb.data != "q1:plus_reminder":
                allowed_msg_ids = {mid for mid in (q1_msg_id, reminder_msg_id) if mid}
                if allowed_msg_ids and cb.message.message_id not in allowed_msg_ids:
                    await cb.answer("Неактуально", show_alert=False)
                    return

            if cb.data == "q1:minus":
                ok, popup = apply_minus(db, sess.session_id, user.id)
                await cb.answer(popup, show_alert=False)

            elif cb.data in ("q1:plus", "q1:plus_reminder"):
                ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
                ok, popup = apply_plus(db, sess.session_id, user.id)
                if cb.data == "q1:plus_reminder":
                    mark_reminder_ack(
                        db,
                        chat_id=chat_id,
                        user_id=user.id,
                        session_date=sess.session_date,
                        message_id=cb.message.message_id,
                    )
                if ok and now_in_tz(chat.timezone).time().hour < 11:
                    popup = "Кофейку и цигарку бахнул? Красава"
                await cb.answer(popup, show_alert=False)

                if ok:
                    # Q2/Q3 обновляем ниже единым best-effort блоком.
                    # Здесь не дергаем сеть, чтобы не откатывать poops_n из-за edit/send ошибки.
                    pass

            else:  # q1:remind
                if now_in_tz(chat.timezone).time().hour >= 22:
                    await cb.answer("После 22:00 напоминалка уже отправлена", show_alert=False)
                else:
                    ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
                    ok, popup = toggle_remind(db, sess.session_id, user.id)
                    await cb.answer(popup, show_alert=False)

            state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user.id})
            reconcile_events_count(
                db,
                session_id=sess.session_id,
                user_id=user.id,
                poops_n=int(state.poops_n) if state else 0,
            )

        # обновляем Q1 (всегда)
            text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
            has_any_members = "Участники:" in text
            show_remind = now_in_tz(chat.timezone).time().hour < 22
            try:
                if q1_msg_id:
                    await cb.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=q1_msg_id,
                        text=text,
                        reply_markup=q1_keyboard(has_any_members, show_remind=show_remind),
                    )
                else:
                    sent = await cb.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=q1_keyboard(has_any_members, show_remind=show_remind),
                    )
                    set_session_message_id(db, sess.session_id, "Q1", sent.message_id)
                    q1_msg_id = sent.message_id
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.exception("Failed to edit Q1 message: %s", e)

            if cb.data in {"q1:plus", "q1:plus_reminder", "q1:minus"}:
                try:
                    await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)
                except Exception:
                    logger.exception("Failed to refresh Q2/Q3 after Q1 action")

        # если есть напоминалка — обновляем в ней статус и счетчики
            if cb.data == "q1:plus_reminder" and not reminder_msg_id:
                # self-heal mapping if message id record was lost
                reminder_msg_id = cb.message.message_id
                set_command_message_id(db, chat_id, 0, REMINDER22_COMMAND, sess.session_date, reminder_msg_id)
            if reminder_msg_id:
                reminder_text = build_reminder_22_text(db, sess.session_id)
                if reminder_text:
                    try:
                        await cb.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=reminder_msg_id,
                            text=reminder_text,
                            parse_mode="HTML",
                            reply_markup=reminder_keyboard(),
                        )
                    except TelegramBadRequest as e:
                        if "message is not modified" not in str(e).lower():
                            logger.exception("Failed to edit reminder message: %s", e)
    except Exception:
        logger.exception("Unhandled exception in q1_callbacks")
        try:
            await cb.answer("Ошибка, попробуй ещё раз", show_alert=False)
        except Exception:
            pass
