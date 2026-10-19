"""Question text shared by Telegram and Mini App; no Telegram client dependency."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import ChatMember, PoopEvent, SessionUserState, User
from app.services.q1_service import mention
from app.services.repo_service import get_session_message_id

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


def render_q2_private_text(db: Session, session_id: int, user_id: int, event_n: int) -> str:
    state = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    total = int(state.poops_n) if state else 0
    target_n = max(1, min(int(event_n), total if total > 0 else 1))
    evt = db.scalar(
        select(PoopEvent)
        .where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id, PoopEvent.event_n == target_n)
    )
    choice = _q2_choice_from_bristol(evt.bristol if evt else None)
    icon = Q2_EMOJI.get(choice, "❔")
    return (
        "🧻 Уточняющий вопрос: Бристоль\n"
        f"Событие #{target_n} из {max(total, 1)}\n\n"
        f"Текущее значение: {icon}\n\n"
        "Выбери, как было:"
    )


def render_q3_private_text(db: Session, session_id: int, user_id: int, event_n: int) -> str:
    state = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    total = int(state.poops_n) if state else 0
    target_n = max(1, min(int(event_n), total if total > 0 else 1))
    evt = db.scalar(
        select(PoopEvent)
        .where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id, PoopEvent.event_n == target_n)
    )
    icon = Q3_EMOJI.get(evt.feeling if evt else None, "❔")
    return (
        "😮‍💨 Уточняющий вопрос: Самочувствие\n"
        f"Событие #{target_n} из {max(total, 1)}\n\n"
        f"Текущее значение: {icon}\n\n"
        "Как ощущения после этого?"
    )


def should_show_q2_q3_button(
    db: Session,
    chat_q2_q3_enabled: bool,
    session_id: int,
    *,
    is_private_chat: bool = False,
) -> bool:
    if is_private_chat:
        return False
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


def q2_choice_from_bristol(value: int | None) -> str | None:
    return _q2_choice_from_bristol(value)


def _collect_people_and_events(db: Session, chat_id: int, session_id: int):
    user_ids = list(db.scalars(select(ChatMember.user_id).where(
        ChatMember.chat_id == chat_id).order_by(ChatMember.joined_at.asc())))
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids)))} if user_ids else {}
    events_by_user = {uid: [] for uid in user_ids}
    if user_ids:
        events = db.scalars(select(PoopEvent).where(
            PoopEvent.session_id == session_id, PoopEvent.origin_chat_id == chat_id,
            PoopEvent.user_id.in_(user_ids)).order_by(PoopEvent.user_id, PoopEvent.event_n, PoopEvent.id))
        for event in events:
            events_by_user[event.user_id].append(event)
    return user_ids, users, events_by_user


def render_q2_text(db: Session, chat_id: int, session_id: int) -> str:
    lines = [Q2_TEXT, "", "Участники:"]
    user_ids, users, events_by_user = _collect_people_and_events(db, chat_id, session_id)
    for uid in user_ids:
        user = users.get(uid)
        if user is None:
            continue
        events = events_by_user[uid]
        if not events:
            lines.append(f"{mention(user)} — —")
            continue

        parts: list[str] = []
        for ev in events:
            n = ev.event_n
            choice = _q2_choice_from_bristol(ev.bristol if ev else None)
            icon = Q2_EMOJI.get(choice, "❔")
            parts.append(f"#{n} {icon}")
        lines.append(f"{mention(user)} — {' | '.join(parts)}")
    return "\n".join(lines)


def render_q3_text(db: Session, chat_id: int, session_id: int) -> str:
    lines = [Q3_TEXT, "", "Участники:"]
    user_ids, users, events_by_user = _collect_people_and_events(db, chat_id, session_id)
    for uid in user_ids:
        user = users.get(uid)
        if user is None:
            continue
        events = events_by_user[uid]
        if not events:
            lines.append(f"{mention(user)} — —")
            continue

        parts: list[str] = []
        for ev in events:
            n = ev.event_n
            icon = Q3_EMOJI.get(ev.feeling if ev else None, "❔")
            parts.append(f"#{n} {icon}")
        lines.append(f"{mention(user)} — {' | '.join(parts)}")
    return "\n".join(lines)


