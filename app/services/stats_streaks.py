from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PoopEvent
from app.db.models import Session as DaySession
from app.db.models import SessionUserState, User


def calc_above_percent(value: int, all_values: list[int]) -> int | None:
    if not all_values:
        return None
    less = sum(1 for v in all_values if v < value)
    eq = sum(1 for v in all_values if v == value)
    return int(round(100.0 * (less + 0.5 * eq) / len(all_values)))


def best_streak_from_days(days: list[date]) -> int:
    if not days:
        return 0
    run = 1
    best = 1
    for i in range(1, len(days)):
        if days[i] == (days[i - 1] + timedelta(days=1)):
            run += 1
        else:
            run = 1
        if run > best:
            best = run
    return best


def streak_until_yesterday(days: list[date], today: date) -> int:
    if not days:
        return 0
    yesterday = today - timedelta(days=1)
    if days[-1] != yesterday:
        return 0
    run = 1
    idx = len(days) - 2
    while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
        run += 1
        idx -= 1
    return run


def current_streak_from_days(days: list[date], today: date) -> int:
    if not days:
        return 0
    has_today = days[-1] == today
    hist_days = days[:-1] if has_today else days
    streak_yesterday = streak_until_yesterday(hist_days, today)
    if not has_today:
        return streak_yesterday
    return streak_yesterday + 1 if streak_yesterday > 0 else 1


def compute_user_chat_streak_live(db: Session, chat_id: int, user_id: int, today: date) -> int:
    days = _fetch_chat_origin_days(db, chat_id, [user_id], today)
    user_days = days.get((chat_id, user_id), [])
    return current_streak_from_days(user_days, today)


def compute_user_chat_best_streak_live(db: Session, chat_id: int, user_id: int, today: date) -> int:
    days = _fetch_chat_origin_days(db, chat_id, [user_id], today)
    user_days = days.get((chat_id, user_id), [])
    return best_streak_from_days(user_days)


def _fetch_chat_origin_days(
    db: Session,
    chat_id: int,
    user_ids: list[int],
    today: date,
) -> dict[tuple[int, int], list[date]]:
    """Batch-запрос для получения дней активностей пользователей в чате.
    
    Возвращает dict {(chat_id, user_id): [dates]} для избежания N+1 запросов.
    """
    if not user_ids:
        return {}
    
    rows = db.execute(
        select(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id == chat_id,
            DaySession.session_date <= today,
            PoopEvent.user_id.in_(user_ids),
            PoopEvent.origin_chat_id == chat_id,
        )
        .group_by(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .order_by(DaySession.chat_id.asc(), PoopEvent.user_id.asc(), DaySession.session_date.asc())
    ).all()

    days_by_user_chat: dict[tuple[int, int], list[date]] = {}
    for cid, uid, d in rows:
        key = (int(cid), int(uid))
        days_by_user_chat.setdefault(key, []).append(d)
    
    return days_by_user_chat


def compute_chat_user_streaks_live(db: Session, chat_ids: list[int], today: date) -> dict[tuple[int, int], int]:
    if not chat_ids:
        return {}
    rows = db.execute(
        select(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id.in_(chat_ids),
            DaySession.session_date <= today,
            PoopEvent.origin_chat_id == DaySession.chat_id,
        )
        .group_by(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .order_by(DaySession.chat_id.asc(), PoopEvent.user_id.asc(), DaySession.session_date.asc())
    ).all()

    days_by_user_chat: dict[tuple[int, int], list[date]] = {}
    for chat_id, user_id, d in rows:
        key = (int(chat_id), int(user_id))
        days_by_user_chat.setdefault(key, []).append(d)

    return {key: current_streak_from_days(days, today) for key, days in days_by_user_chat.items()}


def compute_chat_user_streaks_live_per_chat_today(
    db: Session,
    chat_ids: list[int],
    today_by_chat: dict[int, date],
) -> dict[tuple[int, int], int]:
    if not chat_ids or not today_by_chat:
        return {}

    max_today = max(today_by_chat.values())
    rows = db.execute(
        select(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id.in_(chat_ids),
            DaySession.session_date <= max_today,
            PoopEvent.origin_chat_id == DaySession.chat_id,
        )
        .group_by(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .order_by(DaySession.chat_id.asc(), PoopEvent.user_id.asc(), DaySession.session_date.asc())
    ).all()

    days_by_user_chat: dict[tuple[int, int], list[date]] = {}
    for chat_id, user_id, d in rows:
        cid = int(chat_id)
        if cid not in today_by_chat:
            continue
        if d > today_by_chat[cid]:
            continue
        key = (cid, int(user_id))
        days_by_user_chat.setdefault(key, []).append(d)

    return {
        key: current_streak_from_days(days, today_by_chat[key[0]])
        for key, days in days_by_user_chat.items()
    }


def compute_user_global_streak_live(db: Session, user_id: int, today: date) -> int:
    days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(
                SessionUserState.user_id == user_id,
                SessionUserState.poops_n > 0,
                DaySession.session_date < today,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]

    streak_yesterday = 0
    if days and days[-1] == (today - timedelta(days=1)):
        streak_yesterday = 1
        idx = len(days) - 2
        while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
            streak_yesterday += 1
            idx -= 1

    has_today = bool(
        db.scalar(
            select(SessionUserState.user_id)
            .join(DaySession, DaySession.session_id == SessionUserState.session_id)
            .where(
                DaySession.session_date == today,
                SessionUserState.user_id == user_id,
                SessionUserState.poops_n > 0,
            )
            .limit(1)
        )
    )
    if not has_today:
        return streak_yesterday
    return streak_yesterday + 1 if streak_yesterday > 0 else 1


def compute_user_global_best_streak_live(db: Session, user_id: int, today: date) -> int:
    days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(
                SessionUserState.user_id == user_id,
                SessionUserState.poops_n > 0,
                DaySession.session_date <= today,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]
    return best_streak_from_days(days)


def streak_nickname(days: int) -> str:
    if days >= 365:
        return "Легенда стрика"
    if days >= 180:
        return "Полугодовой чемпион"
    if days >= 90:
        return "Квартальный титан"
    if days >= 30:
        return "Месячный монолит"
    if days >= 7:
        return "Железная неделя"
    return "Держит ритм"


def chat_streak_leader(db: Session, chat_id: int, today: date) -> tuple[User | None, int, int] | None:
    streaks_by_user = compute_chat_user_streaks_live(db, [chat_id], today)
    if not streaks_by_user:
        return None

    best_user_id = None
    best_streak = 0
    for (cid, uid), days in streaks_by_user.items():
        if cid != chat_id:
            continue
        if days > best_streak:
            best_streak = days
            best_user_id = uid

    if best_user_id is None or best_streak <= 0:
        return None
    return db.get(User, best_user_id), best_streak, best_user_id


def compute_chat_user_streaks_batch(
    db: Session,
    chat_id: int,
    user_ids: list[int],
    today: date,
) -> dict[int, int]:
    """Batch-версия для получения стриков всех пользователей чата одним запросом.
    
    Возвращает dict {user_id: streak} для избежания N+1 запросов.
    """
    if not user_ids:
        return {}
    
    days_map = _fetch_chat_origin_days(db, chat_id, user_ids, today)
    result: dict[int, int] = {}
    
    for uid in user_ids:
        user_days = days_map.get((chat_id, uid), [])
        result[uid] = current_streak_from_days(user_days, today)
    
    return result


def build_stats_raw_debug_text(db: Session, chat_id: int, user_id: int, today: date) -> str:
    from app.services.stats_service import _bot_start_date, _chat_start_date

    def _fmt_days(days: list[date], *, limit: int = 20) -> str:
        if not days:
            return "нет"
        if len(days) <= limit:
            return ", ".join(d.strftime("%d.%m") for d in days)
        head = ", ".join(d.strftime("%d.%m") for d in days[:10])
        tail = ", ".join(d.strftime("%d.%m") for d in days[-10:])
        return f"{head} ... {tail}"

    def _breakdown(days: list[date], day: date) -> list[str]:
        if not days:
            return [
                "- сегодня: нет",
                "- вчера: нет",
                "- причина: нет активных дней",
            ]

        has_today = days[-1] == day
        yesterday = day - timedelta(days=1)
        has_yesterday = yesterday in set(days)
        lines = [
            f"- сегодня: {'да' if has_today else 'нет'}",
            f"- вчера: {'да' if has_yesterday else 'нет'}",
        ]

        if has_today:
            prev = day
            i = len(days) - 2
            while i >= 0 and days[i] == (prev - timedelta(days=1)):
                prev = days[i]
                i -= 1
            if i < 0:
                lines.append("- причина: разрывов нет, цепочка от первого дня")
            else:
                lines.append(
                    f"- причина: разрыв между {days[i].strftime('%d.%m')} и {(days[i] + timedelta(days=1)).strftime('%d.%m')}"
                )
            return lines

        if days[-1] == yesterday:
            prev = yesterday
            i = len(days) - 2
            while i >= 0 and days[i] == (prev - timedelta(days=1)):
                prev = days[i]
                i -= 1
            if i < 0:
                lines.append("- причина: сегодня нет отметки, но вчерашняя цепочка цельная от первого дня")
            else:
                lines.append(
                    f"- причина: сегодня нет отметки; внутри цепочки разрыв между {days[i].strftime('%d.%m')} и {(days[i] + timedelta(days=1)).strftime('%d.%m')}"
                )
            return lines

        lines.append("- причина: нет отметки вчера/сегодня, текущий стрик обнулен")
        return lines

    chat_start = _chat_start_date(db, chat_id)
    bot_start = _bot_start_date(db)

    origin_days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date <= today,
                PoopEvent.user_id == user_id,
                PoopEvent.origin_chat_id == chat_id,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]

    chat_state_days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date <= today,
                SessionUserState.user_id == user_id,
                SessionUserState.poops_n > 0,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]

    global_state_days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(
                DaySession.session_date <= today,
                SessionUserState.user_id == user_id,
                SessionUserState.poops_n > 0,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]

    origin_curr = current_streak_from_days(origin_days, today)
    origin_best = best_streak_from_days(origin_days)
    chat_curr = current_streak_from_days(chat_state_days, today)
    chat_best = best_streak_from_days(chat_state_days)
    global_curr = current_streak_from_days(global_state_days, today)
    global_best = best_streak_from_days(global_state_days)

    return "\n".join(
        [
            "🧪 Сырые метрики",
            f"today={today.strftime('%d.%m.%Y')} chat_id={chat_id} user_id={user_id}",
            f"chat_start={(chat_start.strftime('%d.%m.%Y') if chat_start else '-')}, bot_start={(bot_start.strftime('%d.%m.%Y') if bot_start else '-')}",
            "",
            "1) Chat origin days (PoopEvent origin_chat_id == chat_id)",
            f"- count={len(origin_days)} curr={origin_curr} best={origin_best}",
            f"- days: {_fmt_days(origin_days)}",
            *_breakdown(origin_days, today),
            "",
            "2) Chat state days (SessionUserState in this chat, poops_n>0)",
            f"- count={len(chat_state_days)} curr={chat_curr} best={chat_best}",
            f"- days: {_fmt_days(chat_state_days)}",
            *_breakdown(chat_state_days, today),
            "",
            "3) Global state days (SessionUserState all chats, poops_n>0)",
            f"- count={len(global_state_days)} curr={global_curr} best={global_best}",
            f"- days: {_fmt_days(global_state_days)}",
            *_breakdown(global_state_days, today),
        ]
    )
