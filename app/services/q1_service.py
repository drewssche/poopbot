from __future__ import annotations

import os
import random
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMember, PoopEvent, Session as DaySession, SessionUserState, User, UserStreak
from app.services.poop_event_service import (
    create_event,
    delete_event,
    list_origin_events,
    normalize_session_user_state_to_origin_chat,
    reconcile_events_count,
)
from app.services.time_service import (
    get_time_slot,
    get_slot_emoji,
    get_slot_title,
    get_dominant_slot,
)

RESTORE_WINDOW_DAYS = 7
RESTORE_MARKER = "__streak_restore_recent__"

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


def _get_user_slot_counts(
    db: Session,
    session_id: int,
    user_id: int,
    tz_name: str = "Europe/Minsk",
    origin_chat_id: int | None = None,
) -> dict[str, int]:
    """Подсчитывает события пользователя по временным слотам."""
    events = db.scalars(
        select(PoopEvent).where(
            PoopEvent.session_id == session_id,
            PoopEvent.user_id == user_id,
            *((
                PoopEvent.origin_chat_id == origin_chat_id,
            ) if origin_chat_id is not None else ()),
        ).order_by(PoopEvent.created_at.asc())
    ).all()
    
    slot_counts = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
    for ev in events:
        if ev.created_at:
            slot = get_time_slot(ev.created_at, tz_name)
            slot_counts[slot] = slot_counts.get(slot, 0) + 1
    
    return slot_counts


def _format_slot_counts(slot_counts: dict[str, int]) -> str:
    """Форматирует счётчики слотов для отображения."""
    parts = []
    for slot in ["night", "morning", "afternoon", "evening"]:
        count = slot_counts.get(slot, 0)
        if count > 0:
            emoji = get_slot_emoji(slot)
            parts.append(f"{emoji} {count}")
    
    return " • ".join(parts) if parts else ""


def _get_user_title(slot_counts: dict[str, int]) -> str:
    """Определяет титул пользователя по слотам."""
    dominant = get_dominant_slot(slot_counts)
    if dominant is None:
        return ""
    return get_slot_title(dominant) + " " + get_slot_emoji(dominant)


def _chat_timezone(db: Session, chat_id: int) -> str:
    chat = db.get(Chat, chat_id)
    return chat.timezone if chat and chat.timezone else "Europe/Minsk"


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


def can_restore_streak_for_date(db: Session, chat_id: int, user_id: int, restore_date: date) -> bool:
    days = _chat_origin_days_before(db, chat_id, user_id, restore_date)
    if not days:
        return False

    day_before_target = restore_date - timedelta(days=1)
    if days[-1] != day_before_target:
        return False

    return not bool(
        db.scalar(
            select(PoopEvent.id)
            .join(DaySession, DaySession.session_id == PoopEvent.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == restore_date,
                PoopEvent.user_id == user_id,
                PoopEvent.origin_chat_id == chat_id,
            )
            .limit(1)
        )
    )


def restore_streak_target_date(db: Session, chat_id: int, user_id: int, current_session_date: date) -> date | None:
    restore_date = current_session_date - timedelta(days=1)
    return restore_date if can_restore_streak_for_date(db, chat_id, user_id, restore_date) else None


def should_show_restore_streak_button(
    db: Session,
    *,
    chat_id: int,
    session_date: date,
    viewer_user_id: int | None,
    is_private_chat: bool,
) -> bool:
    if (os.getenv("Q1_RESTORE_BUTTON_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False

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


def restore_streak_for_date(db: Session, chat_id: int, user_id: int, restore_date: date) -> tuple[bool, str]:
    if not can_restore_streak_for_date(db, chat_id, user_id, restore_date):
        return False, "Тебе нечего восстанавливать"

    current_session_date = restore_date + timedelta(days=1)
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
            start_at=datetime.now(UTC),
            end_at=datetime.now(UTC),
        )
        db.add(sess)
        db.flush()

    state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user_id})
    if state is None:
        state = SessionUserState(session_id=sess.session_id, user_id=user_id, poops_n=1)
        db.add(state)
    elif int(state.poops_n or 0) > 0:
        return False, "За нужный день уже есть отметка"
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
    return True, f"Стрик за {restore_date.strftime('%d.%m')} восстановлен"


def undo_restore_for_date(db: Session, chat_id: int, user_id: int, restore_date: date) -> tuple[bool, str]:
    sess = db.scalar(
        select(DaySession).where(
            DaySession.chat_id == chat_id,
            DaySession.session_date == restore_date,
        )
    )
    if sess is None:
        return False, "За этот день нечего отменять"

    state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user_id})
    if state is None or int(state.poops_n or 0) <= 0:
        return False, "За этот день нечего отменять"

    while int(state.poops_n or 0) > 0:
        delete_event(db, session_id=sess.session_id, user_id=user_id, event_n=int(state.poops_n))
        state.poops_n -= 1

    state.achievement_text = None
    state.bristol = None
    state.feeling = None

    _refresh_user_streak_cache_before_current_day(
        db,
        chat_id=chat_id,
        user_id=user_id,
        current_session_date=restore_date + timedelta(days=1),
    )
    return True, f"Восстановление за {restore_date.strftime('%d.%m')} отменено"


def restore_streak_for_user(db: Session, chat_id: int, user_id: int, current_session_date: date) -> tuple[bool, str]:
    restore_date = restore_streak_target_date(db, chat_id, user_id, current_session_date)
    if restore_date is None:
        return False, "Тебе нечего восстанавливать"
    changed, message = restore_streak_for_date(db, chat_id, user_id, restore_date)
    if not changed:
        return changed, message
    return True, "Стрик за вчера восстановлен"


def restore_recent_streak_window(
    db: Session,
    *,
    chat_id: int,
    user_id: int,
    current_session_date: date,
    days_back: int = RESTORE_WINDOW_DAYS,
) -> tuple[bool, str]:
    window_start = current_session_date - timedelta(days=max(1, days_back))
    restore_days: list[date] = []
    for offset in range(max(1, days_back), 0, -1):
        restore_date = current_session_date - timedelta(days=offset)
        has_mark = bool(
            db.scalar(
                select(PoopEvent.id)
                .join(DaySession, DaySession.session_id == PoopEvent.session_id)
                .where(
                    DaySession.chat_id == chat_id,
                    DaySession.session_date == restore_date,
                    PoopEvent.user_id == user_id,
                    PoopEvent.origin_chat_id == chat_id,
                )
                .limit(1)
            )
        )
        if not has_mark:
            restore_days.append(restore_date)

    if not restore_days:
        return False, f"За последние {days_back} дней пропусков нет"

    for restore_date in restore_days:
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
                start_at=datetime.now(UTC),
                end_at=datetime.now(UTC),
            )
            db.add(sess)
            db.flush()

        state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user_id})
        if state is None:
            state = SessionUserState(session_id=sess.session_id, user_id=user_id, poops_n=1, achievement_text=RESTORE_MARKER)
            db.add(state)
        else:
            state.poops_n = max(1, int(state.poops_n or 0))
            state.achievement_text = RESTORE_MARKER
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
    return True, f"Восстановлено {len(restore_days)} дн. за последние {days_back} дней"


def undo_recent_streak_window(
    db: Session,
    *,
    chat_id: int,
    user_id: int,
    current_session_date: date,
    days_back: int = RESTORE_WINDOW_DAYS,
) -> tuple[bool, str]:
    undone_days = 0
    for offset in range(max(1, days_back), 0, -1):
        restore_date = current_session_date - timedelta(days=offset)
        sess = db.scalar(
            select(DaySession).where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == restore_date,
            )
        )
        if sess is None:
            continue
        state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user_id})
        if state is None or state.achievement_text != RESTORE_MARKER or int(state.poops_n or 0) <= 0:
            continue

        while int(state.poops_n or 0) > 0:
            delete_event(db, session_id=sess.session_id, user_id=user_id, event_n=int(state.poops_n))
            state.poops_n -= 1
        state.achievement_text = None
        state.bristol = None
        state.feeling = None
        undone_days += 1

    if undone_days == 0:
        return False, f"За последние {days_back} дней нечего отменять"

    _refresh_user_streak_cache_before_current_day(
        db,
        chat_id=chat_id,
        user_id=user_id,
        current_session_date=current_session_date,
    )
    return True, f"Отменено {undone_days} дн. восстановления"


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
    if origin_chat_id is None:
        origin_chat_id = int(db.scalar(select(DaySession.chat_id).where(DaySession.session_id == session_id)) or 0)

    normalize_session_user_state_to_origin_chat(
        db,
        session_id=session_id,
        user_id=user_id,
        origin_chat_id=origin_chat_id,
    )

    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None:
        st = SessionUserState(session_id=session_id, user_id=user_id, poops_n=0)
        db.add(st)
        db.flush()

    if st.poops_n >= 10:
        return False, "Я тебе не верю"

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
    origin_chat_id = int(db.scalar(select(DaySession.chat_id).where(DaySession.session_id == session_id)) or 0)
    normalize_session_user_state_to_origin_chat(
        db,
        session_id=session_id,
        user_id=user_id,
        origin_chat_id=origin_chat_id,
    )

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
    tz_name = _chat_timezone(db, chat_id)

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
    chat_today_counts = {
        int(uid): int(count)
        for uid, count in db.execute(
            select(PoopEvent.user_id, func.count(PoopEvent.id))
            .where(
                PoopEvent.session_id == session_id,
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
        poops = int(chat_today_counts.get(int(uid), 0))

        # Считаем стрик
        streak_val = _streak_until_yesterday(chat_days_by_user.get(int(uid), []), session_date)
        if int(uid) in chat_today_positive_user_ids:
            streak_val += 1 if streak_val > 0 else 1

        if poops == 0:
            lines.append(f"{mention(u)} — — | стрик {streak_val} дн.")
        else:
            # Есть отметки — показываем слоты + титул + стрик
            slot_counts = _get_user_slot_counts(
                db,
                session_id,
                int(uid),
                tz_name,
                origin_chat_id=chat_id,
            )
            slot_display = _format_slot_counts(slot_counts)
            title = _get_user_title(slot_counts)
            
            if slot_display and title and title != "Участник":
                lines.append(f"{mention(u)} — {slot_display} | {title} • стрик {streak_val} дн.")
            elif slot_display:
                lines.append(f"{mention(u)} — {slot_display} | стрик {streak_val} дн.")
            else:
                lines.append(f"{mention(u)} — 💩({poops}) | стрик {streak_val} дн.")

    return "\n".join(lines)


def render_q1_private(db: Session, chat_id: int, session_id: int, user_id: int, session_date: date) -> str:
    date_str = session_date.strftime("%d.%m.%y")
    tz_name = _chat_timezone(db, chat_id)
    events = list_origin_events(db, session_id, user_id, chat_id)
    poops = len(events)

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

    # Считаем слоты для лички
    slot_counts = _get_user_slot_counts(db, session_id, user_id, tz_name, origin_chat_id=chat_id)
    slot_display = _format_slot_counts(slot_counts)
    title = _get_user_title(slot_counts)

    lines = [
        f"💩 Твоя личная сессия ({date_str})",
        "Нажми +💩, чтобы добавить отметку.",
        "",
        f"Итого: 💩({poops})",
    ]

    # Добавляем слоты если есть
    if poops > 0 and slot_display:
        lines.append(f"Ритм: {slot_display}")
        if title and title != "Участник":
            lines.append(f"Титул: {title}")

    lines.append(f"Стрик в этой личке: {chat_streak} дн.")

    if poops <= 0:
        lines.extend(["", "Сегодня пока без отметок."])
        return "\n".join(lines)

    lines.extend(["", "Сегодня:"])
    for ev in events:
        b_icon = BRISTOL_EMOJI.get(int(ev.bristol), "❔") if ev.bristol is not None else "❔"
        f_icon = FEELING_EMOJI.get(ev.feeling, "❔") if ev.feeling else "❔"
        lines.append(f"- #{int(ev.event_n)} {b_icon} • {f_icon}")

    return "\n".join(lines)
