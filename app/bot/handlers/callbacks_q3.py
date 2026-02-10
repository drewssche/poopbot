from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.db.models import SessionUserState

from app.services.repo_service import (
    upsert_chat,
    upsert_user,
    get_or_create_session,
    get_session_message_id,
)
from app.services.time_service import get_session_window
from app.services.rate_limit_service import check_rate_limit
from app.services.q1_service import render_q1
from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.q3 import q3_keyboard

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None

Q3_TEXT = "😮‍💨 Как прошёл процесс?"


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _map_choice_to_feeling(choice: str) -> str:
    # храним enum: great | ok | bad
    if choice == "great":
        return "great"
    if choice == "ok":
        return "ok"
    return "bad"


@router.callback_query(F.data.startswith("q3:"))
async def q3_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user
    choice = cb.data.split(":", 1)[1]  # great | ok | bad

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id)
        window = get_session_window(chat.timezone)

        # единый попап в blocked window
        if window.is_blocked_window:
            await cb.answer("Новая сессия начнётся в 00:05", show_alert=False)
            return

        # антиспам 2 сек
        if not check_rate_limit(db, chat_id=chat_id, user_id=user.id, scope="Q3", cooldown_seconds=2):
            await cb.answer("Не так быстро, здоровяк", show_alert=False)
            return

        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        if sess.status == "closed":
            await cb.answer("Сессия закрыта", show_alert=False)
            return

        # защита от клика по старому/удалённому Q3
        q3_msg_id = get_session_message_id(db, sess.session_id, "Q3")
        if q3_msg_id and cb.message.message_id != q3_msg_id:
            await cb.answer("Неактуально", show_alert=False)
            return

        state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user.id})
        if state is None or state.poops_n <= 0:
            await cb.answer("Ты не какал", show_alert=False)
            return

        state.feeling = _map_choice_to_feeling(choice)
        await cb.answer("Записал", show_alert=False)

        try:
            await cb.message.edit_text(Q3_TEXT, reply_markup=q3_keyboard())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.exception("Failed to edit Q3 text: %s", e)

        # обновить Q1, чтобы появился смайлик ощущения в строке юзера
        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
        if q1_msg_id:
            text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
            has_any_members = "Участники:" in text
            try:
                await cb.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=q1_msg_id,
                    text=text,
                    reply_markup=q1_keyboard(has_any_members),
                )
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "message is not modified" in msg:
                    return
                if "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                    return
                logger.exception("Failed to edit Q1 from Q3: %s", e)
