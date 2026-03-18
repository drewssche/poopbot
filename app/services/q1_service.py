from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMember, PoopEvent, Session as DaySession, SessionUserState, User
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


def _streak_until_yesterday(days: list[date], day: date) -> int:
    if not days:
        return 0
    yesterday = day - timedelta(days=1)
    if days[-1] != yesterday:
        return 0
    run = 1
    idx = len(days) - 2
    while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
        run += 1
        idx -= 1
    return run


def mention(u: User) -> str:
    if bool(getattr(u, "disable_mentions", False)):
        name = " ".join(x for x in [(u.first_name or "").strip(), (u.last_name or "").strip()] if x).strip()
        if not name:
            name = (u.username or "").strip() or f"id:{int(u.user_id)}"
        return name
    if u.username:
        return f"@{u.username}"
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


def apply_plus(db: Session, session_id: int, user_id: int, origin_chat_id: int | None = None) -> tuple[bool, str]:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None:
        st = SessionUserState(session_id=session_id, user_id=user_id, poops_n=0)
        db.add(st)
        db.flush()

    if st.poops_n >= 10:
        return False, "Я тебе не верю"

    if origin_chat_id is None:
        origin_chat_id = int(db.scalar(select(DaySession.chat_id).where(DaySession.session_id == session_id)) or 0)

    prev = st.poops_n
    st.poops_n += 1
    create_event(
        db,
        session_id=session_id,
        user_id=user_id,
        event_n=st.poops_n,
        origin_chat_id=origin_chat_id,
    )

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


def render_q1(db: Session, chat_id: int, session_id: int, session_date: date) -> str:
    date_str = session_date.strftime("%d.%m.%y")

    member_rows = db.execute(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id).order_by(ChatMember.joined_at.asc())
    ).all()

    header = (
        f"💩 Кто сегодня какал? ({date_str})\n"
        "Чтобы попасть в список участников — нажми +1💩."
    )

    if not member_rows:
        return header + "\n(Пока никто не участвует)"

    user_ids = [int(user_id) for (user_id,) in member_rows]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()}
    states = {
        s.user_id: s
        for s in db.scalars(
            select(SessionUserState).where(
                SessionUserState.session_id == session_id,
                SessionUserState.user_id.in_(user_ids),
            )
        ).all()
    }
    chat_today_positive_user_ids = {
        int(uid)
        for uid in db.scalars(
            select(PoopEvent.user_id)
            .join(DaySession, DaySession.session_id == PoopEvent.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == session_date,
                PoopEvent.user_id.in_(user_ids),
                PoopEvent.origin_chat_id == chat_id,
            )
            .group_by(PoopEvent.user_id)
        ).all()
    }
    chat_hist_rows = db.execute(
        select(PoopEvent.user_id, DaySession.session_date)
        .join(DaySession, DaySession.session_id == PoopEvent.session_id)
        .where(
            DaySession.chat_id == chat_id,
            PoopEvent.user_id.in_(user_ids),
            DaySession.session_date < session_date,
            PoopEvent.origin_chat_id == chat_id,
        )
        .group_by(PoopEvent.user_id, DaySession.session_date)
        .order_by(PoopEvent.user_id.asc(), DaySession.session_date.asc())
    ).all()
    chat_days_by_user: dict[int, list[date]] = {int(uid): [] for uid in user_ids}
    for uid, d in chat_hist_rows:
        chat_days_by_user[int(uid)].append(d)

    lines = [header, "", "Участники:"]

    for uid in user_ids:
        u = users.get(uid)
        if not u:
            continue
        st = states.get(uid)
        poops = int(st.poops_n) if st else 0

        status_bits: list[str] = [f"💩({poops})"]

        streak_val = _streak_until_yesterday(chat_days_by_user.get(int(uid), []), session_date)
        if int(uid) in chat_today_positive_user_ids:
            streak_val += 1 if streak_val > 0 else 1
        status_bits.append(f"чатовый стрик {streak_val} дн.")

        lines.append(f"{mention(u)} — {' • '.join(status_bits)}")

    return "\n".join(lines)


def render_q1_private(db: Session, chat_id: int, session_id: int, user_id: int, session_date: date) -> str:
    date_str = session_date.strftime("%d.%m.%y")
    state = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    poops = int(state.poops_n) if state else 0

    events = db.scalars(
        select(PoopEvent)
        .where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id)
        .order_by(PoopEvent.event_n.asc())
    ).all()

    chat_days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date < session_date,
                PoopEvent.user_id == user_id,
                PoopEvent.origin_chat_id == chat_id,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]
    chat_today_positive = bool(
        db.scalar(
            select(PoopEvent.id)
            .join(DaySession, DaySession.session_id == PoopEvent.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == session_date,
                PoopEvent.user_id == user_id,
                PoopEvent.origin_chat_id == chat_id,
            )
            .limit(1)
        )
    )
    chat_streak = _streak_until_yesterday(chat_days, session_date)
    if chat_today_positive:
        chat_streak += 1 if chat_streak > 0 else 1

    lines = [
        f"💩 Твоя личная сессия ({date_str})",
        "Нажми +💩, чтобы добавить отметку.",
        "",
        f"Итого: 💩({poops})",
        f"Стрик в этой личке: {chat_streak} дн.",
    ]

    if poops <= 0:
        lines.extend(["", "Сегодня пока без отметок."])
        return "\n".join(lines)

    lines.extend(["", "Сегодня:"])
    for ev in events:
        b_icon = BRISTOL_EMOJI.get(int(ev.bristol), "❔") if ev.bristol is not None else "❔"
        f_icon = FEELING_EMOJI.get(ev.feeling, "❔") if ev.feeling else "❔"
        lines.append(f"- #{int(ev.event_n)} {b_icon} • {f_icon}")

    return "\n".join(lines)
