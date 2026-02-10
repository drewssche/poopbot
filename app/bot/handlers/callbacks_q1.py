from __future__ import annotations

import asyncio
import logging
import random

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.q2 import q2_keyboard
from app.bot.keyboards.q3 import q3_keyboard

from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.db.models import SessionUserState

from app.services.repo_service import (
    upsert_chat,
    upsert_user,
    ensure_chat_member,
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
)
from app.services.time_service import get_session_window
from app.services.rate_limit_service import check_rate_limit
from app.services.q1_service import render_q1, apply_plus, apply_minus, toggle_remind

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None

Q2_TEXT = "🧻 Бристоль (тип стула)\nВыбери, что было сегодня:"
Q3_TEXT = "😮‍💨 Как прошёл процесс?"


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


async def _ensure_q2_q3_exist(cb: CallbackQuery, db: Session, session_id: int) -> None:
    """
    Self-heal: если Q2/Q3 удалили вручную — восстановим.
    Q3 появляется через 1 секунду после Q2.
    Вызывать только когда в сессии уже есть poops_n>0 хотя бы у кого-то.
    """
    if cb.message is None:
        return

    chat_id = cb.message.chat.id

    # Q2
    q2_id = get_session_message_id(db, session_id, "Q2")
    if q2_id:
        try:
            await cb.bot.edit_message_text(
                chat_id=chat_id,
                message_id=q2_id,
                text=Q2_TEXT,
                reply_markup=q2_keyboard(),
            )
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                q2_id = None
            elif "message is not modified" in msg:
                pass
            else:
                logger.exception("Q2 edit check failed: %s", e)

    if not q2_id:
        q2 = await cb.message.answer(Q2_TEXT, reply_markup=q2_keyboard())
        set_session_message_id(db, session_id, "Q2", q2.message_id)

    # Q3
    q3_id = get_session_message_id(db, session_id, "Q3")
    if q3_id:
        try:
            await cb.bot.edit_message_text(
                chat_id=chat_id,
                message_id=q3_id,
                text=Q3_TEXT,
                reply_markup=q3_keyboard(),
            )
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                q3_id = None
            elif "message is not modified" in msg:
                pass
            else:
                logger.exception("Q3 edit check failed: %s", e)

    if not q3_id:
        await asyncio.sleep(1)
        q3 = await cb.message.answer(Q3_TEXT, reply_markup=q3_keyboard())
        set_session_message_id(db, session_id, "Q3", q3.message_id)


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

        # ✅ FIX: правильный аргумент (как в твоём рабочем коде)
        if not check_rate_limit(db, chat_id=chat_id, user_id=user.id, scope="Q1", cooldown_seconds=2):
            # разные попапы по кнопкам (как ты просил)
            if cb.data in ("q1:plus", "q1:minus"):
                await cb.answer("Так быстро не какают", show_alert=False)
            else:
                await cb.answer("Не так быстро, здоровяк", show_alert=False)
            return

        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        # защита: клики только по актуальному Q1
        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
        if q1_msg_id and cb.message.message_id != q1_msg_id:
            await cb.answer("Неактуально", show_alert=False)
            return

        # было ли poops_n>0 у кого-то ДО обработки (для появления Q2/Q3)
        had_any_poop_before = (
            db.scalar(
                select(func.count())
                .select_from(SessionUserState)
                .where(SessionUserState.session_id == sess.session_id, SessionUserState.poops_n > 0)
            )
            or 0
        ) > 0

        if cb.data == "q1:minus":
            ok, popup = apply_minus(db, sess.session_id, user.id)
            await cb.answer(popup or "", show_alert=False)

        elif cb.data == "q1:plus":
            ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
            ok, popup = apply_plus(db, sess.session_id, user.id)
            await cb.answer(popup or "", show_alert=False)

            # если это первый 💩 в сессии — создаём Q2/Q3 (self-heal тоже тут)
            if ok:
                st = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user.id})
                if st and st.poops_n > 0:
                    # если до этого никто не какал — создаём; если уже было — просто self-heal (на случай удаления)
                    asyncio.create_task(_ensure_q2_q3_exist(cb, db, sess.session_id))

        else:  # q1:remind
            ensure_chat_member(db, chat_id=chat_id, user_id=user.id)
            ok, popup = toggle_remind(db, sess.session_id, user.id)
            await cb.answer(popup, show_alert=False)

        text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
        has_any_members = "Участники:" in text

        try:
            await cb.message.edit_text(text, reply_markup=q1_keyboard(has_any_members))
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.exception("Failed to edit Q1 message: %s", e)
            await cb.answer("Не смог обновить сообщение (см. логи)", show_alert=False)
