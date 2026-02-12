from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.q2 import q2_keyboard
from app.db.engine import make_engine, make_session_factory
from app.db.models import ChatMember, Session as DaySession, SessionMessage, SessionUserState
from app.db.session import db_session
from app.services.poop_event_service import ensure_events_count, list_events
from app.services.q1_service import render_q1
from app.services.q2_q3_service import render_q2_text
from app.services.rate_limit_service import check_rate_limit
from app.services.repo_service import (
    get_or_create_session,
    get_session_message_id,
    upsert_chat,
    upsert_user,
)
from app.services.time_service import get_session_window, now_in_tz

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _map_choice_to_bristol(choice: str) -> int:
    if choice == "12":
        return 2
    if choice == "34":
        return 4
    if choice == "56":
        return 6
    return 7


def _choice_from_bristol(value: int | None) -> str | None:
    if value is None:
        return None
    if value <= 2:
        return "12"
    if value <= 4:
        return "34"
    if value <= 6:
        return "56"
    return "7"


def _choice_to_icon(choice: str | None) -> str:
    return {
        "12": "🧱",
        "34": "🍌",
        "56": "🍦",
        "7": "💦",
    }.get(choice or "", "❔")


def _parse_q2(data: str, poops_n: int) -> tuple[int, str | None]:
    parts = data.split(":")
    target_event_n = max(1, poops_n)
    if len(parts) == 2 and parts[1] in {"12", "34", "56", "7"}:
        return target_event_n, parts[1]
    if len(parts) == 3 and parts[1] == "sel":
        try:
            target_event_n = int(parts[2])
        except ValueError:
            pass
        return target_event_n, None
    if len(parts) == 4 and parts[1] == "set":
        try:
            target_event_n = int(parts[2])
        except ValueError:
            pass
        choice = parts[3] if parts[3] in {"12", "34", "56", "7"} else None
        return target_event_n, choice
    return target_event_n, None


@router.callback_query(F.data.startswith("q2:"))
async def q2_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id)
        window = get_session_window(chat.timezone)

        if window.is_blocked_window:
            await cb.answer("\u041d\u043e\u0432\u0430\u044f \u0441\u0435\u0441\u0441\u0438\u044f \u043d\u0430\u0447\u043d\u0451\u0442\u0441\u044f \u0432 00:05", show_alert=False)
            return

        if not check_rate_limit(db, chat_id=chat_id, user_id=user.id, scope="Q2", cooldown_seconds=2):
            await cb.answer("\u041d\u0435 \u0442\u0430\u043a \u0431\u044b\u0441\u0442\u0440\u043e, \u0437\u0434\u043e\u0440\u043e\u0432\u044f\u043a", show_alert=False)
            return

        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
        sess = db.scalar(
            select(DaySession)
            .join(SessionMessage, SessionMessage.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                SessionMessage.kind == "Q2",
                SessionMessage.message_id == cb.message.message_id,
            )
        )
        if sess is None:
            sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        if sess.status == "closed":
            await cb.answer("\u0421\u0435\u0441\u0441\u0438\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u0430", show_alert=False)
            return

        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
        if not q1_msg_id:
            await cb.answer("\u041d\u0435\u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e", show_alert=False)
            return

        q2_msg_id = get_session_message_id(db, sess.session_id, "Q2")
        if q2_msg_id and cb.message.message_id != q2_msg_id:
            await cb.answer("\u041d\u0435\u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e", show_alert=False)
            return

        state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user.id})
        if state is None or state.poops_n <= 0:
            await cb.answer("\u0422\u044b \u043d\u0435 \u043a\u0430\u043a\u0430\u043b", show_alert=False)
            return

        ensure_events_count(db, sess.session_id, user.id, state.poops_n)
        events = list_events(db, sess.session_id, user.id)
        events_by_n = {int(e.event_n): e for e in events}

        selected_n, selected_choice = _parse_q2(cb.data, int(state.poops_n))
        if selected_n < 1 or selected_n > int(state.poops_n):
            selected_n = int(state.poops_n)

        if selected_choice:
            evt = events_by_n.get(selected_n)
            if evt is not None:
                evt.bristol = _map_choice_to_bristol(selected_choice)
                state.bristol = evt.bristol
                await cb.answer(f"Записал для тебя: #{selected_n} {_choice_to_icon(selected_choice)}", show_alert=False)
        else:
            await cb.answer()

        evt = events_by_n.get(selected_n)
        active_choice = _choice_from_bristol(evt.bristol if evt else None)

        try:
            await cb.message.edit_text(
                render_q2_text(db, chat_id, sess.session_id),
                reply_markup=q2_keyboard(selected_choice=active_choice),
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.exception("Failed to edit Q2 text: %s", e)

        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
        if q1_msg_id:
            text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
            has_any_members = bool(
                db.scalar(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == chat_id))
            )
            try:
                await cb.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=q1_msg_id,
                    text=text,
                    reply_markup=q1_keyboard(
                        has_any_members,
                        show_remind=now_in_tz(chat.timezone).time().hour < 22,
                    ),
                )
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "message is not modified" in msg:
                    return
                if "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                    return
                logger.exception("Failed to edit Q1 from Q2: %s", e)
