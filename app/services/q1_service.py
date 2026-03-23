from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMember, PoopEvent, Session as DaySession, SessionUserState, User, UserStreak
from app.services.poop_event_service import create_event, delete_event, reconcile_events_count


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


def _trailing_streak(days: list[date]) -> int:
    if not days:
        return 0
    run = 1
    idx = len(days) - 2
    while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
        run += 1
        idx -= 1
    return run


def _chat_origin_days_before(db: Session, chat_id: int, user_id: int, before_date: date) -> list[date]:
    return [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date < before_date,
                PoopEvent.user_id == user_id,
                PoopEvent.origin_chat_id == chat_id,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]


def restore_streak_target_date(db: Session, chat_id: int, user_id: int, current_session_date: date) -> date | None:
    days = _chat_origin_days_before(db, chat_id, user_id, current_session_date)
    if not days:
        return None

    day_before_yesterday = current_session_date - timedelta(days=2)
    if days[-1] != day_before_yesterday:
        return None

    return current_session_date - timedelta(days=1)


def should_show_restore_streak_button(
    db: Session,
    *,
    chat_id: int,
    session_date: date,
    viewer_user_id: int | None,
    is_private_chat: bool,
) -> bool:
    if is_private_chat:
        if viewer_user_id is None:
            return False
        return restore_streak_target_date(db, chat_id, viewer_user_id, session_date) is not None

    user_ids = db.scalars(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)).all()
    return any(
        restore_streak_target_date(db, chat_id, int(user_id), session_date) is not None
        for user_id in user_ids
    )


def _refresh_user_streak_cache_before_current_day(
    db: Session,
    *,
    chat_id: int,
    user_id: int,
    current_session_date: date,
) -> None:
    streak = db.get(UserStreak, {"chat_id": chat_id, "user_id": user_id})
    if streak is None:
        streak = UserStreak(chat_id=chat_id, user_id=user_id, current_streak=0, last_poop_date=None)
        db.add(streak)
        db.flush()

    days = _chat_origin_days_before(db, chat_id, user_id, current_session_date)
    if not days:
        streak.current_streak = 0
        streak.last_poop_date = None
        return

    last_day = days[-1]
    streak.last_poop_date = last_day
    streak.current_streak = _trailing_streak(days) if last_day == (current_session_date - timedelta(days=1)) else 0


def restore_streak_for_user(db: Session, chat_id: int, user_id: int, current_session_date: date) -> tuple[bool, str]:
    restore_date = restore_streak_target_date(db, chat_id, user_id, current_session_date)
    if restore_date is None:
        return False, "Тебе нечего восстанавливать"

    sess = db.scalar(
        select(DaySession).where(
            DaySession.chat_id == chat_id,
            DaySession.session_date == restore_date,
        )
    )
    if sess is None:
        sess = DaySession(
            chat_id=chat_id,
            session_date=restore_date,
            status="closed",
            start_at=datetime.utcnow(),
            end_at=datetime.utcnow(),
        )
        db.add(sess)
        db.flush()

    state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user_id})
    if state is None:
        state = SessionUserState(session_id=sess.session_id, user_id=user_id, poops_n=1)
        db.add(state)
    elif int(state.poops_n or 0) > 0:
        return False, "За вчера уже есть отметка"
    else:
        state.poops_n = 1
        state.achievement_text = None
        state.bristol = None
        state.feeling = None

    reconcile_events_count(
        db,
        session_id=sess.session_id,
        user_id=user_id,
        poops_n=int(state.poops_n or 0),
        origin_chat_id=chat_id,
    )
    _refresh_user_streak_cache_before_current_day(
        db,
        chat_id=chat_id,
        user_id=user_id,
        current_session_date=current_session_date,
    )
    return True, "Стрик за вчера восстановлен"


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
