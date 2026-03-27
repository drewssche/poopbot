from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.q2 import q2_keyboard
from app.db.engine import make_engine, make_session_factory
from app.db.models import CommandMessage, Session as DaySession, SessionMessage, SessionUserState
from app.db.session import db_session
from app.services.command_message_service import (
    get_any_command_message_id,
    get_command_message_id,
)
from app.services.poop_event_service import reconcile_events_count
from app.services.q1_service import (
    apply_minus,
    apply_plus,
    render_q1,
    render_q1_private,
    restore_streak_for_user,
    should_show_restore_streak_button,
)
from app.services.q2_q3_service import ensure_q2_q3_exist, render_q2_private_text, should_show_q2_q3_button
from app.services.rate_limit_service import check_rate_limit
from app.services.reminder_service import LATE_REMINDER_COMMAND
from app.services.time_service import get_time_slot, get_slot_popup
from app.services.repo_service import (
    ensure_chat_member,
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
    upsert_chat,
    upsert_user,
)
from app.services.time_service import get_session_window, now_in_tz

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None


def _is_stale_callback_error(e: TelegramBadRequest) -> bool:
    msg = str(e).lower()
    return "query is too old" in msg or "query id is invalid" in msg


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _resolve_reminder_context(db, chat_id: int, current_sess, cb: CallbackQuery, command: str) -> bool:
    current_q1_msg_id = get_session_message_id(db, current_sess.session_id, "Q1")
    current_reminder_msg_id = get_command_message_id(
        db, chat_id, 0, command, current_sess.session_date
    ) or get_any_command_message_id(db, chat_id, command, current_sess.session_date)

    row_by_msg = db.scalar(
        select(CommandMessage).where(
            CommandMessage.chat_id == chat_id,
            CommandMessage.command == command,
            CommandMessage.message_id == cb.message.message_id,
        )
    )

    is_current_by_msg_id = current_reminder_msg_id is not None and cb.message.message_id == current_reminder_msg_id
    is_current_by_reply = (
        cb.message.reply_to_message is not None
        and current_q1_msg_id is not None
        and cb.message.reply_to_message.message_id == current_q1_msg_id
    )
    is_current_by_mapping = row_by_msg is not None and row_by_msg.session_date == current_sess.session_date

    return is_current_by_msg_id or is_current_by_reply or is_current_by_mapping


@router.callback_query(F.data.in_({"q1:plus", "q1:minus", "q1:plus_late", "q1:restore_streak"}))
async def q1_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user
    is_private_chat = cb.message.chat.type == "private"

    try:
        with db_session(_session_factory) as db:
            chat = upsert_chat(db, chat_id=chat_id)
            window = get_session_window(chat.timezone)

            if window.is_blocked_window:
                await cb.answer("Новая сессия начнётся в 00:05", show_alert=False)
                return

            rate_limit_scope = "Q1_RESTORE" if cb.data == "q1:restore_streak" else "Q1"
            cooldown_seconds = 4 if cb.data == "q1:restore_streak" else 2
            if not check_rate_limit(
                db,
                chat_id=chat_id,
                user_id=user.id,
                scope=rate_limit_scope,
                cooldown_seconds=cooldown_seconds,
            ):
                await cb.answer("Не так быстро, здоровяк", show_alert=False)
                return

            upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
            db.flush()
            current_sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

            if cb.data == "q1:plus_late":
                reminder_command = LATE_REMINDER_COMMAND
                if not _resolve_reminder_context(db, chat_id, current_sess, cb, reminder_command):
                    await cb.answer("Неактуально", show_alert=False)
                    return
                sess = current_sess
            else:
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
                    await cb.answer("Неактуально", show_alert=False)
                    return

            if sess.status == "closed":
                await cb.answer("Сессия закрыта", show_alert=False)
                return

            q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
            late_msg_id = (
                cb.message.message_id
                if cb.data == "q1:plus_late"
                else (
                    get_command_message_id(db, chat_id, 0, LATE_REMINDER_COMMAND, sess.session_date)
                    or get_any_command_message_id(db, chat_id, LATE_REMINDER_COMMAND, sess.session_date)
                )
            )

            if cb.data != "q1:plus_late":
                allowed_msg_ids = {mid for mid in (q1_msg_id, late_msg_id) if mid}
                if allowed_msg_ids and cb.message.message_id not in allowed_msg_ids:
                    await cb.answer("Неактуально", show_alert=False)
                    return

            if cb.data == "q1:restore_streak":
                ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
                changed, popup = restore_streak_for_user(db, chat_id, user.id, current_sess.session_date)
                await cb.answer(popup, show_alert=False)
            elif cb.data == "q1:minus":
                changed, popup = apply_minus(db, sess.session_id, user.id)
                await cb.answer(popup, show_alert=False)
            else:
                ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
                changed, popup = apply_plus(db, sess.session_id, user.id, origin_chat_id=chat_id)
                
                # Контекстный попап по времени
                if changed:
                    now = now_in_tz(chat.timezone)
                    current_hour = now.hour
                    current_slot = get_time_slot(now, chat.timezone)
                    slot_popup = get_slot_popup(current_slot, current_hour)
                    if slot_popup:
                        popup = slot_popup
                
                await cb.answer(popup, show_alert=False)

            if cb.data != "q1:restore_streak":
                state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user.id})
                reconcile_events_count(
                    db,
                    session_id=sess.session_id,
                    user_id=user.id,
                    poops_n=int(state.poops_n) if state else 0,
                    origin_chat_id=chat_id,
                )

            db.commit()

            text = (
                render_q1_private(db, chat_id=chat_id, session_id=sess.session_id, user_id=user.id, session_date=sess.session_date)
                if is_private_chat
                else render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=sess.session_date)
            )
            has_any_members = True if is_private_chat else ("Участники:" in text)
            try:
                if q1_msg_id:
                    await cb.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=q1_msg_id,
                        text=text,
                        reply_markup=q1_keyboard(
                            has_any_members,
                            show_remind=now_in_tz(chat.timezone).time().hour < 22,
                            show_restore_streak_button=should_show_restore_streak_button(
                                db,
                                chat_id=chat_id,
                                session_date=sess.session_date,
                                viewer_user_id=user.id if is_private_chat else None,
                                is_private_chat=is_private_chat,
                            ),
                            show_q2_q3_button=should_show_q2_q3_button(
                                db,
                                chat_q2_q3_enabled=bool(chat.q2_q3_enabled),
                                session_id=sess.session_id,
                                is_private_chat=is_private_chat,
                            ),
                        ),
                    )
                else:
                    sent = await cb.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=q1_keyboard(
                            has_any_members,
                            show_remind=now_in_tz(chat.timezone).time().hour < 22,
                            show_restore_streak_button=should_show_restore_streak_button(
                                db,
                                chat_id=chat_id,
                                session_date=sess.session_date,
                                viewer_user_id=user.id if is_private_chat else None,
                                is_private_chat=is_private_chat,
                            ),
                            show_q2_q3_button=should_show_q2_q3_button(
                                db,
                                chat_q2_q3_enabled=bool(chat.q2_q3_enabled),
                                session_id=sess.session_id,
                                is_private_chat=is_private_chat,
                            ),
                        ),
                    )
                    set_session_message_id(db, sess.session_id, "Q1", sent.message_id)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.exception("Failed to edit Q1 message: %s", e)

            if is_private_chat and cb.data not in {"q1:minus", "q1:restore_streak"} and changed:
                state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user.id})
                target_n = max(1, int(state.poops_n)) if state is not None else 1
                try:
                    await cb.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=q1_msg_id or cb.message.message_id,
                        text=render_q2_private_text(db, sess.session_id, user.id, target_n),
                        reply_markup=q2_keyboard(private_flow=True, event_n=target_n),
                    )
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e).lower():
                        logger.exception("Failed to open private Q2 flow: %s", e)
                return

            if bool(chat.q2_q3_enabled) and not is_private_chat:
                try:
                    await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)
                except Exception:
                    logger.exception("Failed to refresh Q2/Q3 after Q1 action")

    except TelegramBadRequest as e:
        if _is_stale_callback_error(e):
            return
        logger.exception("Unhandled telegram error in q1_callbacks")
        try:
            await cb.answer("Ошибка, попробуй ещё раз", show_alert=False)
        except TelegramBadRequest as answer_err:
            if _is_stale_callback_error(answer_err):
                return
            raise
    except Exception:
        logger.exception("Unhandled exception in q1_callbacks")
        try:
            await cb.answer("Ошибка, попробуй ещё раз", show_alert=False)
        except TelegramBadRequest as e:
            if _is_stale_callback_error(e):
                return
        except Exception:
            pass


@router.callback_query(F.data == "q1:q2q3")
async def q1_open_q2_q3(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id

    try:
        with db_session(_session_factory) as db:
            chat = upsert_chat(db, chat_id=chat_id)
            window = get_session_window(chat.timezone)
            if window.is_blocked_window:
                await cb.answer("Новая сессия начнётся в 00:05", show_alert=False)
                return

            sess = db.scalar(
                select(DaySession)
                .join(SessionMessage, SessionMessage.session_id == DaySession.session_id)
                .where(
                    DaySession.chat_id == chat_id,
                    SessionMessage.kind == "Q1",
                    SessionMessage.message_id == cb.message.message_id,
                )
            )
            if sess is None or sess.status == "closed":
                await cb.answer("Неактуально", show_alert=False)
                return

            if cb.message.chat.type == "private":
                await cb.answer("В личке используй +💩", show_alert=False)
                return

            if not check_rate_limit(db, chat_id=chat_id, user_id=cb.from_user.id, scope="Q1_Q2Q3", cooldown_seconds=4):
                await cb.answer("Не так быстро, здоровяк", show_alert=False)
                return

            q2_id = get_session_message_id(db, sess.session_id, "Q2")
            q3_id = get_session_message_id(db, sess.session_id, "Q3")
            if q2_id or q3_id:
                q1_text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=sess.session_date)
                has_any_members = "Участники:" in q1_text
                try:
                    await cb.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=get_session_message_id(db, sess.session_id, "Q1") or cb.message.message_id,
                        text=q1_text,
                        reply_markup=q1_keyboard(
                            has_any_members,
                            show_restore_streak_button=should_show_restore_streak_button(
                                db,
                                chat_id=chat_id,
                                session_date=sess.session_date,
                                viewer_user_id=cb.from_user.id if cb.message.chat.type == "private" else None,
                                is_private_chat=cb.message.chat.type == "private",
                            ),
                            show_q2_q3_button=False,
                        ),
                    )
                except TelegramBadRequest:
                    pass
                await cb.answer("Уточняющие вопросы уже опубликованы", show_alert=False)
                return

            await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)

            q1_text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=sess.session_date)
            has_any_members = "Участники:" in q1_text
            q1_id = get_session_message_id(db, sess.session_id, "Q1")
            if q1_id:
                try:
                    await cb.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=q1_id,
                        text=q1_text,
                        reply_markup=q1_keyboard(
                            has_any_members,
                            show_restore_streak_button=should_show_restore_streak_button(
                                db,
                                chat_id=chat_id,
                                session_date=sess.session_date,
                                viewer_user_id=cb.from_user.id if cb.message.chat.type == "private" else None,
                                is_private_chat=cb.message.chat.type == "private",
                            ),
                            show_q2_q3_button=False,
                        ),
                    )
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e).lower():
                        logger.exception("Failed to hide Q2/Q3 button after manual publish: %s", e)

            await cb.answer("Уточняющие вопросы опубликованы", show_alert=False)
    except Exception:
        logger.exception("Unhandled exception in q1_open_q2_q3")
        try:
            await cb.answer("Ошибка, попробуй ещё раз", show_alert=False)
        except Exception:
            pass
