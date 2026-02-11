from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.db.models import SessionUserState

from app.bot.keyboards.q1 import q1_keyboard

from app.services.repo_service import (
    upsert_chat,
    upsert_user,
    ensure_chat_member,
    get_or_create_session,
    get_session_message_id,
)
from app.services.time_service import get_session_window
from app.services.rate_limit_service import check_rate_limit
from app.services.q1_service import render_q1, apply_plus, apply_minus, toggle_remind
from app.services.q2_q3_service import ensure_q2_q3_exist

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None



def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


@router.callback_query(F.data.in_({"q1:plus", "q1:minus", "q1:remind"}))
async def q1_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user

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
        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        if sess.status == "closed":
            await cb.answer("Сессия закрыта", show_alert=False)
            return

        # клик только по актуальному Q1
        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
        if q1_msg_id and cb.message.message_id != q1_msg_id:
            await cb.answer("Неактуально", show_alert=False)
            return

        # totalPoops до обработки (чтобы понять 0->1)
        total_before = db.scalar(
            select(func.coalesce(func.sum(SessionUserState.poops_n), 0)).where(SessionUserState.session_id == sess.session_id)
        ) or 0

        if cb.data == "q1:minus":
            ok, popup = apply_minus(db, sess.session_id, user.id)
            await cb.answer(popup, show_alert=False)

        elif cb.data == "q1:plus":
            ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
            ok, popup = apply_plus(db, sess.session_id, user.id)
            await cb.answer(popup, show_alert=False)

            if ok:
                total_after = total_before + 1  # гарантированно +1 к сумме
                # Q2/Q3 должны уже существовать (создаются при появлении Q1),
                # здесь оставляем self-heal на случай удаления сообщений.
                if total_before == 0 and total_after > 0:
                    await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)
                else:
                    # self-heal только если их реально нет/удалены
                    q2_id = get_session_message_id(db, sess.session_id, "Q2")
                    q3_id = get_session_message_id(db, sess.session_id, "Q3")
                    if not q2_id or not q3_id:
                        await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)

        else:  # q1:remind
            ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
            ok, popup = toggle_remind(db, sess.session_id, user.id)
            await cb.answer(popup, show_alert=False)

        # обновляем Q1 (всегда)
        text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
        has_any_members = "Участники:" in text
        try:
            await cb.message.edit_text(text, reply_markup=q1_keyboard(has_any_members))
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.exception("Failed to edit Q1 message: %s", e)
