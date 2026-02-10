from __future__ import annotations

from datetime import date
from typing import Optional
import html as py_html

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionUserState, User, ChatMember, UserStreak
from app.services.achievement_service import pick_achievement


BRISTOL_EMOJI = {
    1: "🧱", 2: "🧱",
    3: "🍌", 4: "🍌",
    5: "🍦", 6: "🍦",
    7: "💦",
}

FEELING_EMOJI = {
    "great": "😇",
    "ok": "😐",
    "bad": "😫",
}


def mention(user: User) -> str:
    """
    HTML-safe упоминание:
    - если username: @username (экранируем)
    - иначе: кликабельный mention по user_id
    """
    if user.username:
        return py_html.escape(f"@{user.username}")

    name = " ".join([p for p in [user.first_name, user.last_name] if p]) or f"User{user.user_id}"
    safe_name = py_html.escape(name)
    return f'<a href="tg://user?id={user.user_id}">{safe_name}</a>'


def render_q1(db: Session, chat_id: int, session_id: int, session_date: date) -> str:
    header = f"💩 Кто сегодня какал? ({session_date.strftime('%d.%m.%y')})"
    hint = "Чтобы попасть в список участников — нажми +1💩."

    rows = (
        db.execute(
            select(User, UserStreak)
            .join(ChatMember, ChatMember.user_id == User.user_id)
            .join(UserStreak, (UserStreak.user_id == User.user_id) & (UserStreak.chat_id == ChatMember.chat_id))
            .where(ChatMember.chat_id == chat_id)
        )
        .all()
    )

    if not rows:
        return f"{header}\n{hint}\n\n(Пока никто не участвует)"

    states = {
        s.user_id: s
        for s in db.scalars(select(SessionUserState).where(SessionUserState.session_id == session_id)).all()
    }

    items: list[tuple[int, str, str]] = []
    for user, streak in rows:
        st = states.get(user.user_id)
        poops_n = st.poops_n if st else 0

        ach = st.achievement_text if (st and st.poops_n > 0) else None
        remind = "⏳" if (st and st.remind_22) else None
        bristol = BRISTOL_EMOJI.get(st.bristol) if (st and st.bristol) else None
        feeling = FEELING_EMOJI.get(st.feeling) if (st and st.feeling) else None

        parts = []
        if ach:
            parts.append(f'«{py_html.escape(ach)}»')
        parts.append(f"— 💩({poops_n})")
        if bristol:
            parts.append(bristol)
        if feeling:
            parts.append(feeling)
        parts.append(f"• стрик {streak.current_streak} дн.")
        if remind:
            parts.append(remind)

        m = mention(user)
        items.append((poops_n, m, " ".join(parts)))

    # сортировка: по какашкам убыв., потом по тексту упоминания
    items.sort(key=lambda x: (-x[0], x[1].lower()))

    lines = ["Участники:"]
    for i, (_, m, rest) in enumerate(items, start=1):
        lines.append(f"{i}) {m} {rest}")

    return f"{header}\n{hint}\n\n" + "\n".join(lines)


def apply_plus(db: Session, session_id: int, user_id: int) -> tuple[bool, Optional[str]]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None:
        st = SessionUserState(
            session_id=session_id,
            user_id=user_id,
            poops_n=0,
            achievement_text=None,
            remind_22=False,
            bristol=None,
            feeling=None,
        )
        db.add(st)

    if st.poops_n >= 10:
        return False, "Я тебе не верю"

    st.poops_n += 1
    if st.poops_n == 1 and not st.achievement_text:
        st.achievement_text = pick_achievement(st.poops_n)

    if 1 <= st.poops_n <= 3:
        return True, "Принял"
    if 4 <= st.poops_n <= 7:
        return True, "Ох, вот это ты даёшь"
    return True, "WOW"


def apply_minus(db: Session, session_id: int, user_id: int) -> tuple[bool, Optional[str]]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None or st.poops_n <= 0:
        return False, "Нельзя вкакаться"

    st.poops_n -= 1
    if st.poops_n <= 0:
        st.poops_n = 0
        st.achievement_text = None
    return True, None


def toggle_remind(db: Session, session_id: int, user_id: int) -> tuple[bool, str]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None:
        st = SessionUserState(
            session_id=session_id,
            user_id=user_id,
            poops_n=0,
            achievement_text=None,
            remind_22=False,
            bristol=None,
            feeling=None,
        )
        db.add(st)

    st.remind_22 = not st.remind_22
    return True, ("Записал" if st.remind_22 else "Убрал")
