from __future__ import annotations

import random
from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, ChatMember, SessionUserState, UserStreak
from app.services.poop_event_service import create_event, delete_event


BRISTOL_EMOJI = {
    1: "🧱",
    2: "🧱",
    3: "🍌",
    4: "🍌",
    5: "🍦",
    6: "🍦",
    7: "💦",
}

FEELING_EMOJI = {
    "great": "😇",
    "ok": "😐",
    "bad": "😫",
}


def mention(u: User) -> str:
    if u.username:
        return f"@{u.username}"
    # Без @ тэгнуть нельзя — показываем имя как есть (как ты хотел), но это не “тег”.
    name = (u.first_name or "").strip()
    if not name:
        name = "Безымянный"
    return name


def _achievement_pool(n: int) -> list[str]:
    if n == 1:
        return ["Стартанул", "Разминка", "Первый пошёл"]
    if 2 <= n <= 3:
        return ["Стабильный", "По графику", "Режимный"]
    if 4 <= n <= 5:
        return ["Говнопушка!", "Турборежим", "Двигатель прогрет"]
    if 6 <= n <= 7:
        return ["Штормит", "Конвейер", "Многоходовочка"]
    if 8 <= n <= 10:
        return ["Легенда", "Портал открыт", "Гига-режим"]
    return []


def apply_plus(db: Session, session_id: int, user_id: int) -> tuple[bool, str]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None:
        st = SessionUserState(session_id=session_id, user_id=user_id, poops_n=0)
        db.add(st)
        db.flush()

    if st.poops_n >= 10:
        return False, "Я тебе не верю"

    prev = st.poops_n
    st.poops_n += 1
    create_event(db, session_id=session_id, user_id=user_id, event_n=st.poops_n)

    # фиксируем ачивку при первом n>0 (0->1)
    if prev == 0 and st.poops_n > 0:
        pool = _achievement_pool(st.poops_n)
        st.achievement_text = random.choice(pool) if pool else None
    elif st.poops_n > 0 and not st.achievement_text:
        pool = _achievement_pool(st.poops_n)
        st.achievement_text = random.choice(pool) if pool else None

    n = st.poops_n
    if 1 <= n <= 3:
        return True, "Принял"
    if 4 <= n <= 7:
        return True, "Ох, вот это ты даёшь"
    return True, "WOW"


def apply_minus(db: Session, session_id: int, user_id: int) -> tuple[bool, str]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None or st.poops_n <= 0:
        return False, "Нельзя вкакаться"

    delete_event(db, session_id=session_id, user_id=user_id, event_n=st.poops_n)
    st.poops_n -= 1
    if st.poops_n == 0:
        st.achievement_text = None
        st.bristol = None
        st.feeling = None
    return True, "Ок"


def toggle_remind(db: Session, session_id: int, user_id: int) -> tuple[bool, str]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None:
        st = SessionUserState(session_id=session_id, user_id=user_id, poops_n=0)
        db.add(st)
        db.flush()

    st.remind_22 = not bool(st.remind_22)
    return True, ("Записал" if st.remind_22 else "Убрал")


def render_q1(db: Session, chat_id: int, session_id: int, session_date: date) -> str:
    date_str = session_date.strftime("%d.%m.%y")

    members = db.scalars(
        select(ChatMember).where(ChatMember.chat_id == chat_id).order_by(ChatMember.joined_at.asc())
    ).all()

    header = (
        f"💩 Кто сегодня какал? ({date_str})\n"
        f"Чтобы попасть в список участников — нажми +1💩.\n"
    )

    if not members:
        return header + "\n(Пока никто не участвует)"

    # подтягиваем юзеров и состояния
    user_ids = [m.user_id for m in members]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()}

    states = {
        s.user_id: s
        for s in db.scalars(select(SessionUserState).where(SessionUserState.session_id == session_id)).all()
    }

    streaks = {
        s.user_id: s
        for s in db.scalars(select(UserStreak).where(UserStreak.chat_id == chat_id)).all()
    }

    lines = [header, "", "Участники:"]

    for uid in user_ids:
        u = users.get(uid)
        if not u:
            continue
        st = states.get(uid)
        poops = int(st.poops_n) if st else 0

        parts: list[str] = []
        name = mention(u)
        parts.append(name)

        # статусные штуки собираем через " • "
        status_bits: list[str] = []
        status_bits.append(f"💩({poops})")

        # ⏳ если подписан
        if st and st.remind_22:
            status_bits.append("⏳")

        streak_row = streaks.get(uid)
        streak_val = streak_row.current_streak if streak_row else 0
        # Отображаем "прогноз" текущего стрика до закрытия дня:
        # если сегодня уже есть poops_n > 0, показываем значение как после закрытия сессии.
        if poops > 0:
            yesterday = session_date - timedelta(days=1)
            if streak_row and streak_row.last_poop_date == yesterday:
                streak_val = streak_row.current_streak + 1
            else:
                streak_val = 1
        status_bits.append(f"стрик {streak_val} дн.")

        line = " — " + " • ".join(status_bits)
        lines.append(parts[0] + line)

    return "\n".join(lines)
