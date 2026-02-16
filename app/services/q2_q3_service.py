from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.keyboards.q2 import q2_keyboard
from app.bot.keyboards.q3 import q3_keyboard
from app.db.models import ChatMember, PoopEvent, SessionUserState, User
from app.services.q1_service import mention
from app.services.repo_service import get_session_message_id, set_session_message_id

logger = logging.getLogger(__name__)

Q2_TEXT = (
    "🧻 Бристоль (тип стула)\n"
    'Узнать о <a href="https://ru.wikipedia.org/wiki/Бристольская_шкала_формы_кала">шкале Бристоля</a>\n\n'
    "Твой выбор применяется к твоему последнему 💩.\n\n"
    "Выбери, что было сегодня:"
)
Q3_TEXT = "Как после этого? Выбери состояние по ощущениям.\n\nТвой выбор применяется к твоему последнему 💩."

Q2_EMOJI = {
    "12": "🧱",
    "34": "🍌",
    "56": "🍦",
    "7": "💦",
}

Q3_EMOJI = {
    "great": "😇",
    "ok": "😐",
    "bad": "😫",
}


def should_show_q2_q3_button(db: Session, chat_q2_q3_enabled: bool, session_id: int) -> bool:
    if bool(chat_q2_q3_enabled):
        return False
    q2_id = get_session_message_id(db, session_id, "Q2")
    q3_id = get_session_message_id(db, session_id, "Q3")
    return not bool(q2_id or q3_id)


def _q2_choice_from_bristol(value: int | None) -> str | None:
    if value is None:
        return None
    if value <= 2:
        return "12"
    if value <= 4:
        return "34"
    if value <= 6:
        return "56"
    return "7"


def _collect_people_and_state(db: Session, chat_id: int, session_id: int) -> tuple[list[int], dict[int, User], dict[int, SessionUserState], dict[tuple[int, int], PoopEvent]]:
    members = db.scalars(
        select(ChatMember).where(ChatMember.chat_id == chat_id).order_by(ChatMember.joined_at.asc())
    ).all()
    user_ids = [int(m.user_id) for m in members]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()} if user_ids else {}
    states = {
        s.user_id: s for s in db.scalars(select(SessionUserState).where(SessionUserState.session_id == session_id)).all()
    }
    events = db.scalars(
        select(PoopEvent)
        .where(PoopEvent.session_id == session_id, PoopEvent.user_id.in_(user_ids))
        .order_by(PoopEvent.user_id.asc(), PoopEvent.event_n.asc())
    ).all() if user_ids else []
    events_map = {(int(e.user_id), int(e.event_n)): e for e in events}
    return user_ids, users, states, events_map


def render_q2_text(db: Session, chat_id: int, session_id: int) -> str:
    lines = [Q2_TEXT, "", "Участники:"]
    user_ids, users, states, events_map = _collect_people_and_state(db, chat_id, session_id)
    for uid in user_ids:
        user = users.get(uid)
        if user is None:
            continue
        state = states.get(uid)
        poops = int(state.poops_n) if state else 0
        if poops <= 0:
            lines.append(f"{mention(user)} — —")
            continue

        parts: list[str] = []
        for n in range(1, poops + 1):
            ev = events_map.get((uid, n))
            choice = _q2_choice_from_bristol(ev.bristol if ev else None)
            icon = Q2_EMOJI.get(choice, "❔")
            parts.append(f"#{n} {icon}")
        lines.append(f"{mention(user)} — {' | '.join(parts)}")
    return "\n".join(lines)


def render_q3_text(db: Session, chat_id: int, session_id: int) -> str:
    lines = [Q3_TEXT, "", "Участники:"]
    user_ids, users, states, events_map = _collect_people_and_state(db, chat_id, session_id)
    for uid in user_ids:
        user = users.get(uid)
        if user is None:
            continue
        state = states.get(uid)
        poops = int(state.poops_n) if state else 0
        if poops <= 0:
            lines.append(f"{mention(user)} — —")
            continue

        parts: list[str] = []
        for n in range(1, poops + 1):
            ev = events_map.get((uid, n))
            icon = Q3_EMOJI.get(ev.feeling if ev else None, "❔")
            parts.append(f"#{n} {icon}")
        lines.append(f"{mention(user)} — {' | '.join(parts)}")
    return "\n".join(lines)


async def ensure_q2_q3_exist(bot: Bot, db: Session, chat_id: int, session_id: int) -> None:
    q1_id = get_session_message_id(db, session_id, "Q1")
    if not q1_id:
        return

    q2_id = get_session_message_id(db, session_id, "Q2")
    q2_alive = False
    if q2_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=q2_id,
                text=render_q2_text(db, chat_id, session_id),
                reply_markup=q2_keyboard(),
            )
            q2_alive = True
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                q2_alive = True
            elif "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                q2_alive = False
            else:
                logger.exception("Q2 edit check failed: %s", e)

    if not q2_alive:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=render_q2_text(db, chat_id, session_id),
            reply_markup=q2_keyboard(),
        )
        set_session_message_id(db, session_id, "Q2", sent.message_id)

    q3_id = get_session_message_id(db, session_id, "Q3")
    q3_alive = False
    if q3_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=q3_id,
                text=render_q3_text(db, chat_id, session_id),
                reply_markup=q3_keyboard(),
            )
            q3_alive = True
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                q3_alive = True
            elif "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                q3_alive = False
            else:
                logger.exception("Q3 edit check failed: %s", e)

    if not q3_alive:
        await asyncio.sleep(1)
        sent = await bot.send_message(
            chat_id=chat_id,
            text=render_q3_text(db, chat_id, session_id),
            reply_markup=q3_keyboard(),
        )
        set_session_message_id(db, session_id, "Q3", sent.message_id)
