from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Chat, PoopEvent
from app.db.models import Session as DaySession
from app.db.models import SessionUserState, User
from app.services.q1_service import mention
from app.services.time_service import now_in_tz


@dataclass(frozen=True)
class Range:
    start: date
    end: date  # inclusive


def period_to_range(today: date, period: str) -> Range:
    if period == "today":
        return Range(today, today)
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return Range(start, start + timedelta(days=6))
    if period == "month":
        start = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return Range(start, end)
    if period == "year":
        return Range(date(today.year, 1, 1), date(today.year, 12, 31))
    return Range(date(1970, 1, 1), today)


def previous_period_range(today: date, period: str) -> Range:
    curr = period_to_range(today, period)
    if period == "week":
        end = curr.start - timedelta(days=1)
        return Range(end - timedelta(days=6), end)
    if period == "month":
        prev_month_last = curr.start - timedelta(days=1)
        start = prev_month_last.replace(day=1)
        end = prev_month_last.replace(day=calendar.monthrange(prev_month_last.year, prev_month_last.month)[1])
        return Range(start, end)
    if period == "year":
        y = today.year - 1
        return Range(date(y, 1, 1), date(y, 12, 31))
    if period == "today":
        prev = today - timedelta(days=1)
        return Range(prev, prev)
    prev = curr.start - timedelta(days=1)
    return Range(date(1970, 1, 1), prev)


def period_label(period: str) -> str:
    if period == "week":
        return "за неделю"
    if period == "month":
        return "за месяц"
    if period == "year":
        return "за год"
    if period == "today":
        return "за день"
    return "за всё время"


GRAMS_PER_POOP_ESTIMATE = 150.0
LITERS_PER_FLUSH_ESTIMATE = 6.0
GALLONS_PER_LITER = 0.264172


def estimate_waste_metrics(poops_n: int) -> tuple[float, float, float]:
    n = max(0, int(poops_n))
    mass_g = float(n) * GRAMS_PER_POOP_ESTIMATE
    water_l = float(n) * LITERS_PER_FLUSH_ESTIMATE
    water_gal = water_l * GALLONS_PER_LITER
    return mass_g, water_l, water_gal


def format_mass(mass_g: float) -> str:
    if mass_g >= 1_000_000.0:
        return f"{mass_g / 1_000_000.0:.2f} т"
    if mass_g >= 1_000.0:
        return f"{mass_g / 1_000.0:.2f} кг"
    return f"{int(round(mass_g))} г"


def format_water(water_l: float, water_gal: float) -> str:
    return f"{water_l:.0f} л ({water_gal:.1f} гал)"


@dataclass(frozen=True)
class ChatPeriodMetrics:
    total_poops: int
    active_participants: int
    active_days_count: int
    period_days: int


def _sessions_in_range(db: Session, chat_id: int | None, r: Range) -> list[DaySession]:
    stmt = select(DaySession).where(DaySession.session_date >= r.start, DaySession.session_date <= r.end)
    if chat_id is not None:
        stmt = stmt.where(DaySession.chat_id == chat_id)
    return list(db.scalars(stmt).all())


def _bot_start_date(db: Session) -> date | None:
    return db.scalar(select(func.min(DaySession.session_date)))


def _chat_start_date(db: Session, chat_id: int) -> date | None:
    return db.scalar(select(func.min(DaySession.session_date)).where(DaySession.chat_id == chat_id))


def _chat_origin_events_in_range(
    db: Session,
    chat_id: int,
    r: Range,
    *,
    user_id: int | None = None,
) -> list[tuple[date, int, int, int | None, str | None]]:
    stmt = (
        select(
            DaySession.session_date,
            PoopEvent.user_id,
            PoopEvent.event_n,
            PoopEvent.bristol,
            PoopEvent.feeling,
        )
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id == chat_id,
            DaySession.session_date >= r.start,
            DaySession.session_date <= r.end,
            PoopEvent.origin_chat_id == chat_id,
        )
    )
    if user_id is not None:
        stmt = stmt.where(PoopEvent.user_id == user_id)
    return list(db.execute(stmt).all())


def compute_chat_period_metrics(db: Session, chat_id: int, r: Range, user_id: int | None = None) -> ChatPeriodMetrics:
    rows = _chat_origin_events_in_range(db, chat_id, r, user_id=user_id)
    total_poops = len(rows)
    if user_id is None:
        active_participants = len({int(uid) for _d, uid, _n, _b, _f in rows})
    else:
        active_participants = 1 if total_poops > 0 else 0
    active_days_count = len({d for d, _uid, _n, _b, _f in rows})

    return ChatPeriodMetrics(
        total_poops=int(total_poops),
        active_participants=int(active_participants),
        active_days_count=int(active_days_count),
        period_days=(r.end - r.start).days + 1,
    )


def _bristol_bucket(bristol: int | None) -> str | None:
    if bristol is None:
        return None
    if bristol <= 2:
        return "🧱"
    if bristol <= 4:
        return "🍌"
    if bristol <= 6:
        return "🍦"
    return "💦"


def _bristol_score(bristol: int | None) -> int | None:
    if bristol is None:
        return None
    if bristol <= 2:
        return 1
    if bristol <= 4:
        return 2
    if bristol <= 6:
        return 3
    return 4


def _bristol_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    val = max(1, min(4, int(round(avg_score))))
    return {1: "🧱", 2: "🍌", 3: "🍦", 4: "💦"}[val]


def _feeling_emoji(feeling: str | None) -> str | None:
    if feeling == "great":
        return "😇"
    if feeling == "ok":
        return "😐"
    if feeling == "bad":
        return "😫"
    return None


def _feeling_score(feeling: str | None) -> int | None:
    if feeling == "bad":
        return 1
    if feeling == "ok":
        return 2
    if feeling == "great":
        return 3
    return None


def _feeling_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    val = max(1, min(3, int(round(avg_score))))
    return {1: "😫", 2: "😐", 3: "😇"}[val]


def _format_period(r: Range) -> str:
    return f"{r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}"


def _format_dist_block(title: str, counts: dict[str, int], legend: dict[str, str]) -> list[str]:
    total = sum(counts.values())
    lines = [title]
    if total <= 0:
        lines.append("- нет данных")
        return lines
    for icon, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        pct = int(round((cnt / total) * 100))
        lines.append(f"- {icon} {legend.get(icon, '')}: {pct}% ({cnt})")
    return lines


def _display_name(user: User | None, fallback_user_id: int) -> str:
    if user is None:
        return f"id:{fallback_user_id}"
    return mention(user)


def _per_user_totals_dedup(db: Session, r: Range) -> dict[int, int]:
    sessions = _sessions_in_range(db, None, r)
    if not sessions:
        return {}
    session_ids = [s.session_id for s in sessions]
    day_user_rows = db.execute(
        select(DaySession.session_date, SessionUserState.user_id, SessionUserState.poops_n)
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.session_id.in_(session_ids))
    ).all()
    per_user_day_max: dict[tuple[int, date], int] = {}
    for row in day_user_rows:
        key = (int(row.user_id), row.session_date)
        poops = int(row.poops_n or 0)
        prev = per_user_day_max.get(key)
        if prev is None or poops > prev:
            per_user_day_max[key] = poops

    per_user_total: dict[int, int] = {}
    for (uid, _day), poops in per_user_day_max.items():
        per_user_total[uid] = per_user_total.get(uid, 0) + poops
    return per_user_total


def _current_global_king(db: Session, today: date) -> tuple[int, int] | None:
    bot_start = _bot_start_date(db)
    if bot_start is None:
        return None
    totals = _per_user_totals_dedup(db, Range(bot_start, today))
    if not totals:
        return None
    return sorted(totals.items(), key=lambda x: (-x[1], x[0]))[0]


def _is_user_participant_in_chat(db: Session, chat_id: int, user_id: int) -> bool:
    return bool(
        db.scalar(
            select(PoopEvent.id)
            .join(DaySession, DaySession.session_id == PoopEvent.session_id)
            .where(
                DaySession.chat_id == chat_id,
                PoopEvent.origin_chat_id == chat_id,
                PoopEvent.user_id == user_id,
            )
            .limit(1)
        )
    )


def _calc_above_percent(value: int, all_values: list[int]) -> int | None:
    if not all_values:
        return None
    less = sum(1 for v in all_values if v < value)
    eq = sum(1 for v in all_values if v == value)
    return int(round(100.0 * (less + 0.5 * eq) / len(all_values)))


def _best_streak_from_days(days: list[date]) -> int:
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


def _streak_until_yesterday(days: list[date], today: date) -> int:
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


def _current_streak_from_days(days: list[date], today: date) -> int:
    if not days:
        return 0
    has_today = days[-1] == today
    hist_days = days[:-1] if has_today else days
    streak_yesterday = _streak_until_yesterday(hist_days, today)
    if not has_today:
        return streak_yesterday
    return streak_yesterday + 1 if streak_yesterday > 0 else 1


def _compute_user_chat_streak_live(db: Session, chat_id: int, user_id: int, today: date) -> int:
    days = [
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
    return _current_streak_from_days(days, today)


def _compute_user_chat_best_streak_live(db: Session, chat_id: int, user_id: int, today: date) -> int:
    days = [
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
    return _best_streak_from_days(days)


def _compute_chat_user_streaks_live(db: Session, chat_ids: list[int], today: date) -> dict[tuple[int, int], int]:
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

    return {key: _current_streak_from_days(days, today) for key, days in days_by_user_chat.items()}


def _compute_chat_user_streaks_live_per_chat_today(
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
        key: _current_streak_from_days(days, today_by_chat[key[0]])
        for key, days in days_by_user_chat.items()
    }


def _compute_user_global_streak_live(db: Session, user_id: int, today: date) -> int:
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


def _compute_user_global_best_streak_live(db: Session, user_id: int, today: date) -> int:
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
    return _best_streak_from_days(days)


def _collect_events_map(db: Session, session_ids: list[int], user_id: int | None = None) -> dict[tuple[int, int], list[PoopEvent]]:
    if not session_ids:
        return {}
    stmt = select(PoopEvent).where(PoopEvent.session_id.in_(session_ids))
    if user_id is not None:
        stmt = stmt.where(PoopEvent.user_id == user_id)
    rows = db.scalars(stmt.order_by(PoopEvent.session_id.asc(), PoopEvent.user_id.asc(), PoopEvent.event_n.asc())).all()
    out: dict[tuple[int, int], list[PoopEvent]] = {}
    for row in rows:
        out.setdefault((int(row.session_id), int(row.user_id)), []).append(row)
    return out


def _iter_effective_events(state: SessionUserState, events_map: dict[tuple[int, int], list[PoopEvent]]) -> list[tuple[int | None, str | None]]:
    key = (int(state.session_id), int(state.user_id))
    evs = events_map.get(key)
    if evs:
        return [(e.bristol, e.feeling) for e in evs]
    if int(state.poops_n or 0) > 0:
        return [(state.bristol, state.feeling)]
    return []


BRISTOL_LEGEND = {
    "🧱": "жестко/сухо",
    "🍌": "норма",
    "🍦": "мягко",
    "💦": "водичка",
}

FEELING_LEGEND = {
    "😇": "отлично",
    "😐": "нормально",
    "😫": "плохо",
}

TOP5_ROLES = [
    "Король какашек",
    "Серебряный трон",
    "Бронзовый трон",
    "Мастер потока",
    "Стабильный напор",
]


def _streak_nickname(days: int) -> str:
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


def _chat_streak_leader(db: Session, chat_id: int, today: date) -> tuple[User | None, int, int] | None:
    streaks_by_user = _compute_chat_user_streaks_live(db, [chat_id], today)
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


def build_stats_text_my(db: Session, chat_id: int, user_id: int, today: date, period: str) -> str:
    _ = chat_id
    bot_start = _bot_start_date(db)
    if bot_start is None:
        empty_range = period_to_range(today, period) if period in {"today", "week", "month", "year"} else Range(today, today)
        return (
            "🙋 Моя статистика\n"
            f"Период: {period_label(period)} (по всем чатам, {_format_period(empty_range)})\n\n"
            "Пока нет данных."
        )

    if period in {"today", "week", "month", "year"}:
        r = period_to_range(today, period)
    else:
        r = Range(bot_start, today)
    sessions = _sessions_in_range(db, None, r)
    if not sessions:
        return (
            "🙋 Моя статистика\n"
            f"Период: {period_label(period)} (по всем чатам, {_format_period(r)})\n\n"
            "Пока нет данных."
        )

    session_ids = [s.session_id for s in sessions]
    states = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.user_id == user_id,
        )
    ).all()

    session_date_by_id = {int(s.session_id): s.session_date for s in sessions}
    states_by_day: dict[date, list[SessionUserState]] = {}
    for st in states:
        sid = int(st.session_id)
        if sid not in session_date_by_id:
            continue
        d = session_date_by_id[sid]
        states_by_day.setdefault(d, []).append(st)

    # Deduplicate cross-chat marks in "My" stats:
    # for each day, use max poops_n among chats instead of sum across chats.
    daily_poops: dict[date, int] = {}
    for d, day_states in states_by_day.items():
        daily_poops[d] = max(int(s.poops_n or 0) for s in day_states)

    total_poops = sum(daily_poops.values())
    days_total = (r.end - r.start).days + 1
    avg_per_day = (float(total_poops) / float(days_total)) if days_total > 0 else 0.0

    active_dates = sorted([d for d, n in daily_poops.items() if n > 0])
    days_any = len(active_dates)
    avg_per_active_day = (float(total_poops) / float(days_any)) if days_any > 0 else 0.0
    last_mark_date = active_dates[-1] if active_dates else None

    best_streak_live = _compute_user_global_best_streak_live(db, user_id, today)

    best_day = max(daily_poops.items(), key=lambda x: (x[1], x[0])) if daily_poops else None

    events_map = _collect_events_map(db, session_ids, user_id=user_id)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    # For per-day distributions in "My", take one canonical day state
    # (highest poops_n, then latest session_id) to avoid cross-chat duplicates.
    canonical_states: list[SessionUserState] = []
    for day_states in states_by_day.values():
        canonical_states.append(
            max(day_states, key=lambda s: (int(s.poops_n or 0), int(s.session_id)))
        )

    for st in canonical_states:
        for bristol, feeling in _iter_effective_events(st, events_map):
            b = _bristol_bucket(bristol)
            if b:
                br[b] += 1
            f = _feeling_emoji(feeling)
            if f:
                fe[f] += 1

    streak_val = _compute_user_global_streak_live(db, user_id, today)

    lines = [
        "🙋 Моя статистика",
        f"Период: {period_label(period)} (по всем чатам, {_format_period(r)})",
        "",
        "Твои итоги:",
        f"- Всего: 💩({total_poops})",
        f"- Дней с 💩: {days_any}/{days_total}",
        f"- Текущий глобальный стрик (по всем чатам): {streak_val} дн.",
        f"- Лучший стрик: {best_streak_live} дн.",
        "",
        "Твоя динамика:",
        f"- Среднее за календарный день: {avg_per_day:.2f}",
        f"- Среднее за день с отметкой: {avg_per_active_day:.2f}",
        (
            f"- Самый активный день: {best_day[0].strftime('%d.%m.%y')} (💩({best_day[1]}))"
            if best_day
            else "- Самый активный день: нет данных"
        ),
        (
            f"- Последняя отметка: {last_mark_date.strftime('%d.%m.%y')}"
            if last_mark_date
            else "- Последняя отметка: нет данных"
        ),
        "",
    ]
    mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
    lines.extend(
        [
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
            "",
        ]
    )
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    return "\n".join(lines)



def build_stats_text_chat(
    db: Session, chat_id: int, today: date, period: str, user_id: int | None = None
) -> str:
    is_bounded_period = period in {"today", "week", "month", "year"}
    bounded_range = period_to_range(today, period) if is_bounded_period else None

    if bounded_range is not None:
        r = bounded_range
    else:
        chat_start = _chat_start_date(db, chat_id)
        r = Range(chat_start, today) if chat_start is not None else Range(today, today)

    rows = _chat_origin_events_in_range(db, chat_id, r, user_id=user_id)
    if not rows:
        if chat_id > 0 and user_id is not None:
            return f"💬 В этой личке\nПериод: {period_label(period)} ({_format_period(r)})\n\nПока нет данных."
        return f"👥 В этом чате\nПериод: {period_label(period)} ({_format_period(r)})\n\nПока пусто."

    by_day: dict[date, int] = {}
    by_user: dict[int, int] = {}
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for d, uid, _n, bristol, feeling in rows:
        uid_int = int(uid)
        by_day[d] = by_day.get(d, 0) + 1
        by_user[uid_int] = by_user.get(uid_int, 0) + 1
        b = _bristol_bucket(bristol)
        if b:
            br[b] += 1
        f = _feeling_emoji(feeling)
        if f:
            fe[f] += 1

    total_poops = len(rows)
    active_days = sorted(d for d, cnt in by_day.items() if cnt > 0)
    active_days_count = len(active_days)
    period_days = (r.end - r.start).days + 1
    peak_day = max(by_day.items(), key=lambda x: (x[1], x[0])) if by_day else None

    if chat_id > 0 and user_id is not None:
        days_any = active_days_count
        avg_per_day = (float(total_poops) / float(period_days)) if period_days > 0 else 0.0
        avg_per_active_day = (float(total_poops) / float(days_any)) if days_any > 0 else 0.0
        last_mark_date = active_days[-1] if active_days else None

        # In private-chat stats, keep streaks consistent with the same local-origin dataset
        # used for totals and active days in this block.
        best_streak_live = _best_streak_from_days(active_days)
        streak_val = _current_streak_from_days(active_days, today)

        mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
        lines = [
            "💬 В этой личке",
            f"Период: {period_label(period)} ({_format_period(r)})",
            "",
            "Твои итоги:",
            f"- Всего: 💩({total_poops})",
            f"- Дней с 💩: {days_any}/{period_days}",
            f"- Текущий стрик: {streak_val} дн.",
            f"- Лучший стрик: {best_streak_live} дн.",
            "",
            "Твоя динамика:",
            f"- Среднее за календарный день: {avg_per_day:.2f}",
            f"- Среднее за день с отметкой: {avg_per_active_day:.2f}",
            (
                f"- Самый активный день: {peak_day[0].strftime('%d.%m.%y')} (💩({peak_day[1]}))"
                if peak_day
                else "- Самый активный день: нет данных"
            ),
            (
                f"- Последняя отметка: {last_mark_date.strftime('%d.%m.%y')}"
                if last_mark_date
                else "- Последняя отметка: нет данных"
            ),
            "",
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
            "",
            "Примечание: в этой личке учитываются отметки, сделанные именно здесь.",
            "",
        ]
        lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
        lines.append("")
        lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
        return "\n".join(lines)

    active_participants = len(by_user)
    avg_per_participant = (float(total_poops) / float(active_participants)) if active_participants > 0 else 0.0
    avg_per_active_day = (float(total_poops) / float(active_days_count)) if active_days_count > 0 else 0.0

    participant_rows = sorted(by_user.items(), key=lambda x: (-x[1], x[0]))
    top_rows = participant_rows[:5]
    top_user_ids = [uid for uid, _cnt in top_rows]

    streak_rank: list[tuple[int, int]] = []
    streaks_live = _compute_chat_user_streaks_live(db, [chat_id], today)
    for (cid, uid), days in streaks_live.items():
        if cid != chat_id or days <= 0:
            continue
        streak_rank.append((uid, days))
    streak_rank.sort(key=lambda x: (-x[1], x[0]))
    streak_top3 = streak_rank[:3]

    user_ids = sorted({uid for uid in by_user.keys()} | {uid for uid, _ in streak_top3})
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()} if user_ids else {}

    lines = [
        "👥 В этом чате",
        f"Период: {period_label(period)} ({_format_period(r)})",
        "",
        "Итоги:",
        f"- Всего: 💩({total_poops})",
        f"- Активных участников: {active_participants}",
        f"- Среднее на участника: {avg_per_participant:.2f}",
        f"- Дней с активностью: {active_days_count}/{period_days}",
        f"- Среднее в активный день: {avg_per_active_day:.2f}",
        (
            f"- Пиковый день: {peak_day[0].strftime('%d.%m.%y')} (💩({peak_day[1]}))"
            if peak_day is not None
            else "- Пиковый день: нет данных"
        ),
        "",
        "Топ-5 по количеству:",
    ]

    if top_rows:
        for idx, (uid, cnt) in enumerate(top_rows, start=1):
            user = users.get(uid)
            role = TOP5_ROLES[idx - 1] if idx - 1 < len(TOP5_ROLES) else "Участник рейтинга"
            lines.append(f"- {idx}) {role} — {_display_name(user, uid)} • 💩({cnt})")
    else:
        lines.append("- пока никого в рейтинге")

    global_king = _current_global_king(db, today)
    if global_king is not None:
        king_uid, _king_total = global_king
        if _is_user_participant_in_chat(db, chat_id, king_uid):
            king_user = users.get(king_uid) or db.get(User, king_uid)
            lines.extend(["", f"👑 Титул чата: {TOP5_ROLES[0]} — {_display_name(king_user, king_uid)}"])

    lines.append("")
    lines.append("Топ-3 по стрику:")
    if streak_top3:
        for idx, (uid, days) in enumerate(streak_top3, start=1):
            user = users.get(uid)
            lines.append(f"- {idx}) {_streak_nickname(days)} — {_display_name(user, uid)} ({days} дн.)")
    else:
        lines.append("- пока нет активных стриков")

    mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
    lines.extend(
        [
            "",
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
        ]
    )
    lines.extend(["", "По участникам (оценка):"])
    for uid, cnt in participant_rows:
        user = users.get(uid)
        p_mass_g, p_water_l, p_water_gal = estimate_waste_metrics(cnt)
        lines.append(
            f"- {_display_name(user, uid)}: 💩({cnt}) • насрал примерно {format_mass(p_mass_g)} • воды на смыв {format_water(p_water_l, p_water_gal)}"
        )

    lines.append("")
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    lines.append("")
    lines.append("Примечание: количество и распределения считаются по отметкам, сделанным именно в этом чате; стрики — по дневной активности именно в этом чате.")
    return "\n".join(lines)
def build_stats_text_global(db: Session, user_id: int, today: date, period: str) -> str:
    if period in {"today", "week", "month", "year"}:
        r = period_to_range(today, period)
    else:
        bot_start = _bot_start_date(db)
        r = Range(bot_start, today) if bot_start is not None else Range(today, today)

    sessions = _sessions_in_range(db, None, r)
    if not sessions:
        return f"🌍 Глобальная статистика\nПериод: {period_label(period)} ({_format_period(r)})\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]
    per_user_total = _per_user_totals_dedup(db, r)

    users_count = len(per_user_total)
    total_poops = sum(per_user_total.values())
    avg_per_user = (float(total_poops) / float(users_count)) if users_count > 0 else 0.0

    ranking_rows = sorted(per_user_total.items(), key=lambda x: (-x[1], x[0]))
    totals = [poops for _, poops in ranking_rows]
    my_total = per_user_total.get(user_id, 0)
    my_rank = next((idx for idx, (uid, _) in enumerate(ranking_rows, start=1) if uid == user_id), None)
    above_pct = _calc_above_percent(my_total, totals) if my_rank is not None else None
    top5 = [(TOP5_ROLES[i], poops) for i, (_uid, poops) in enumerate(ranking_rows[:5])]

    projected_streaks_by_user: dict[int, int] = {
        int(uid): _compute_user_global_streak_live(db, int(uid), today)
        for uid in per_user_total.keys()
    }

    states_pos = db.scalars(select(SessionUserState).where(SessionUserState.session_id.in_(session_ids))).all()
    session_date_by_id = {int(s.session_id): s.session_date for s in sessions}
    canonical_state_by_user_day: dict[tuple[int, date], SessionUserState] = {}
    for st in states_pos:
        sid = int(st.session_id)
        d = session_date_by_id.get(sid)
        if d is None:
            continue
        key = (int(st.user_id), d)
        curr = canonical_state_by_user_day.get(key)
        if curr is None:
            canonical_state_by_user_day[key] = st
            continue
        curr_key = (int(curr.poops_n or 0), int(curr.session_id))
        new_key = (int(st.poops_n or 0), int(st.session_id))
        if new_key > curr_key:
            canonical_state_by_user_day[key] = st

    events_map = _collect_events_map(db, session_ids)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    user_br_scores: dict[int, list[int]] = {}
    user_fe_scores: dict[int, list[int]] = {}

    for st in canonical_state_by_user_day.values():
        uid = int(st.user_id)
        for bristol, feeling in _iter_effective_events(st, events_map):
            b = _bristol_bucket(bristol)
            if b:
                br[b] += 1
            f = _feeling_emoji(feeling)
            if f:
                fe[f] += 1

            bs = _bristol_score(bristol)
            if bs is not None:
                user_br_scores.setdefault(uid, []).append(bs)
            fs = _feeling_score(feeling)
            if fs is not None:
                user_fe_scores.setdefault(uid, []).append(fs)

    br_map = {uid: (sum(vals) / len(vals)) for uid, vals in user_br_scores.items() if vals}
    fe_map = {uid: (sum(vals) / len(vals)) for uid, vals in user_fe_scores.items() if vals}

    my_br_avg = br_map.get(user_id)
    my_fe_avg = fe_map.get(user_id)
    my_br_icon = _bristol_from_avg(my_br_avg)
    my_fe_icon = _feeling_from_avg(my_fe_avg)

    my_br_pct = (
        _calc_above_percent(int(round(my_br_avg * 1000)), [int(round(v * 1000)) for v in br_map.values()])
        if my_br_avg is not None
        else None
    )
    my_fe_pct = (
        _calc_above_percent(int(round(my_fe_avg * 1000)), [int(round(v * 1000)) for v in fe_map.values()])
        if my_fe_avg is not None
        else None
    )

    me = db.get(User, user_id)
    me_name = _display_name(me, user_id)

    lines = [
        "🌍 Глобальная статистика",
        f"Период: {period_label(period)} ({_format_period(r)})",
        "",
        "Итоги:",
        f"- Участников: {int(users_count)}",
        f"- Всего: 💩({int(total_poops)})",
        f"- 💩 на 1 участника: {avg_per_user:.2f}",
        "",
        "Топ-5:",
    ]

    if top5:
        for role, poops in top5:
            lines.append(f"- {role} — 💩({poops})")
    else:
        lines.append("- пока нет данных")

    mass_g, water_l, water_gal = estimate_waste_metrics(total_poops)
    lines.extend(
        [
            "",
            "Сколько насрано и сколько воды на смыв (оценка):",
            f"- Насрано примерно: {format_mass(mass_g)} (по 💩({int(total_poops)})).",
            f"- Воды на смыв: {format_water(water_l, water_gal)}.",
        ]
    )
    if ranking_rows:
        king_uid, king_total = ranking_rows[0]
        king_mass_g, king_water_l, king_water_gal = estimate_waste_metrics(king_total)
        lines.append(
            f"- {TOP5_ROLES[0]}: 💩({int(king_total)}) • насрал примерно {format_mass(king_mass_g)} • воды на смыв {format_water(king_water_l, king_water_gal)}"
        )

    lines.extend(["", "Лидеры глобальных стриков:"])
    top_streaks = sorted(
        [(uid, days) for uid, days in projected_streaks_by_user.items() if int(days) > 0],
        key=lambda x: (-x[1], x[0]),
    )[:3]
    if not top_streaks:
        lines.append("- пока нет данных")
    else:
        for idx, (_, days) in enumerate(top_streaks, start=1):
            lines.append(f"- #{idx} {_streak_nickname(int(days))} — {int(days)} дн.")

    lines.extend(["", "Твоя позиция:", f"- {me_name}"])
    if my_rank is None:
        lines.append("- пока не видно в глобальном рейтинге")
    else:
        lines.append(f"- Место: #{my_rank} из {len(ranking_rows)}")
        lines.append(f"- Всего: 💩({my_total})")
        if 1 <= my_rank <= len(TOP5_ROLES):
            lines.append(f"- Текущий титул: {TOP5_ROLES[my_rank - 1]}")
            if my_rank == 1:
                lines.append("- Ты сейчас держишь трон: 👑 Король какашек")
        if above_pct is not None:
            lines.append(f"- Выше {above_pct}% участников")

    lines.append("")
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))

    lines.extend(["", "Твои распределения:"])
    if my_br_pct is None or my_br_icon is None:
        lines.append("- Бристоль: нет данных")
    else:
        lines.append(f"- Бристоль: {my_br_icon} (выше {my_br_pct}%)")

    if my_fe_pct is None or my_fe_icon is None:
        lines.append("- Ощущения: нет данных")
    else:
        lines.append(f"- Ощущения: {my_fe_icon} (выше {my_fe_pct}%)")

    return "\n".join(lines)


def _visible_group_chat_ids(db: Session) -> list[int]:
    return db.scalars(
        select(Chat.chat_id).where(Chat.is_enabled == True, Chat.show_in_global == True, Chat.chat_id < 0)  # noqa: E712
    ).all()


def rank_chat_among_groups_by_total(db: Session, chat_id: int, r: Range) -> tuple[int | None, int]:
    chat_ids = _visible_group_chat_ids(db)
    if not chat_ids:
        return None, 0

    rows = db.execute(
        select(DaySession.chat_id, func.coalesce(func.sum(SessionUserState.poops_n), 0).label("poops"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id.in_(chat_ids),
            DaySession.session_date >= r.start,
            DaySession.session_date <= r.end,
        )
        .group_by(DaySession.chat_id)
    ).all()
    ranking = sorted([(int(row.chat_id), int(row.poops or 0)) for row in rows], key=lambda x: (-x[1], x[0]))
    if not ranking:
        return None, 0
    rank = next((idx for idx, (cid, _total) in enumerate(ranking, start=1) if cid == chat_id), None)
    return rank, len(ranking)


def collect_among_chats_snapshot(db: Session, today: date, r: Range | None = None) -> dict:
    min_bristol_samples = 10
    # Exclude private dialogs (chat_id > 0), keep only group/supergroup chats.
    chat_ids = _visible_group_chat_ids(db)
    if not chat_ids:
        return {
            "top_total": [],
            "top_avg": [],
            "top_streak": [],
            "top_mass": [],
            "total_poops_all": 0,
            "record_day": None,
            "record_days": [],
            "most_liquid": None,
            "most_dry": None,
        }

    sessions_stmt = select(DaySession).where(DaySession.chat_id.in_(chat_ids))
    if r is not None:
        sessions_stmt = sessions_stmt.where(DaySession.session_date >= r.start, DaySession.session_date <= r.end)
    sessions = db.scalars(sessions_stmt).all()
    if not sessions:
        return {
            "top_total": [],
            "top_avg": [],
            "top_streak": [],
            "top_mass": [],
            "total_poops_all": 0,
            "record_day": None,
            "record_days": [],
            "most_liquid": None,
            "most_dry": None,
        }

    session_ids = [int(s.session_id) for s in sessions]

    by_chat_total = db.execute(
        select(DaySession.chat_id, func.coalesce(func.sum(SessionUserState.poops_n), 0).label("poops"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.session_id.in_(session_ids))
        .group_by(DaySession.chat_id)
    ).all()

    by_chat_participants = db.execute(
        select(DaySession.chat_id, func.count(func.distinct(SessionUserState.user_id)).label("participants"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.session_id.in_(session_ids), SessionUserState.poops_n > 0)
        .group_by(DaySession.chat_id)
    ).all()

    participants_map = {int(r.chat_id): int(r.participants or 0) for r in by_chat_participants}
    totals = [(int(r.chat_id), int(r.poops or 0)) for r in by_chat_total]
    total_poops_all = sum(total for _cid, total in totals)
    top_total = sorted(totals, key=lambda x: (-x[1], x[0]))[:5]
    top_mass = [(chat_id, estimate_waste_metrics(total)[0]) for chat_id, total in top_total]

    avg_rows: list[tuple[int, float, int, int]] = []
    for chat_id, total in totals:
        participants = participants_map.get(chat_id, 0)
        if participants <= 0:
            continue
        avg_rows.append((chat_id, float(total) / float(participants), total, participants))
    top_avg = sorted(avg_rows, key=lambda x: (-x[1], x[0]))[:5]

    chat_rows = db.scalars(select(Chat).where(Chat.chat_id.in_(chat_ids))).all()
    tz_by_chat = {int(ch.chat_id): (ch.timezone or "Europe/Minsk") for ch in chat_rows}
    latest_session_date_by_chat: dict[int, date] = {}
    for s in sessions:
        cid = int(s.chat_id)
        prev = latest_session_date_by_chat.get(cid)
        if prev is None or s.session_date > prev:
            latest_session_date_by_chat[cid] = s.session_date

    # For among-chats streak, anchor each chat to its own latest known session date.
    # This keeps the ranking consistent with what users currently see in that chat's Q1.
    today_by_chat: dict[int, date] = {}
    for cid in chat_ids:
        if cid in latest_session_date_by_chat:
            today_by_chat[cid] = latest_session_date_by_chat[cid]
            continue
        today_by_chat[cid] = now_in_tz(tz_by_chat.get(cid, "Europe/Minsk")).date()
    streaks_live = _compute_chat_user_streaks_live_per_chat_today(db, chat_ids, today_by_chat)
    best_streak_by_chat: dict[int, int] = {}
    for (cid, _uid), days in streaks_live.items():
        if days > best_streak_by_chat.get(cid, 0):
            best_streak_by_chat[cid] = days
    top_streak = sorted(
        [(chat_id, days) for chat_id, days in best_streak_by_chat.items() if days > 0],
        key=lambda x: (-x[1], x[0]),
    )[:5]

    day_rows = db.execute(
        select(DaySession.chat_id, DaySession.session_date, func.coalesce(func.sum(SessionUserState.poops_n), 0).label("poops"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.session_id.in_(session_ids))
        .group_by(DaySession.chat_id, DaySession.session_date)
    ).all()
    record_day = None
    record_days: list[tuple[int, date, int]] = []
    if day_rows:
        max_poops = max(int(r.poops or 0) for r in day_rows)
        if max_poops > 0:
            winners = [
                (int(r.chat_id), r.session_date, int(r.poops or 0))
                for r in day_rows
                if int(r.poops or 0) == max_poops
            ]
            winners.sort(key=lambda x: (x[1], x[0]))
            record_days = winners
            record_day = winners[0]

    bristol_rows = db.execute(
        select(DaySession.chat_id, PoopEvent.bristol)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.session_id.in_(session_ids),
            PoopEvent.origin_chat_id == DaySession.chat_id,
            PoopEvent.bristol.is_not(None),
        )
    ).all()
    bristol_by_chat: dict[int, dict[str, int]] = {}
    for chat_id, bristol in bristol_rows:
        cid = int(chat_id)
        b = int(bristol)
        bucket = bristol_by_chat.setdefault(cid, {"total": 0, "liquid": 0, "dry": 0})
        bucket["total"] += 1
        if b >= 6:
            bucket["liquid"] += 1
        if b <= 2:
            bucket["dry"] += 1

    most_liquid = None
    most_dry = None
    liquid_candidates: list[tuple[int, float, int, int]] = []
    dry_candidates: list[tuple[int, float, int, int]] = []
    for cid, v in bristol_by_chat.items():
        total = int(v["total"])
        if total < min_bristol_samples:
            continue
        liquid = int(v["liquid"])
        dry = int(v["dry"])
        liquid_share = (float(liquid) / float(total)) if total > 0 else 0.0
        dry_share = (float(dry) / float(total)) if total > 0 else 0.0
        liquid_candidates.append((cid, liquid_share, liquid, total))
        dry_candidates.append((cid, dry_share, dry, total))
    if liquid_candidates:
        most_liquid = max(liquid_candidates, key=lambda x: (x[1], x[2], -x[0]))
    if dry_candidates:
        most_dry = max(dry_candidates, key=lambda x: (x[1], x[2], -x[0]))

    return {
        "top_total": top_total,
        "top_avg": top_avg,
        "top_streak": top_streak,
        "top_mass": top_mass,
        "total_poops_all": total_poops_all,
        "record_day": record_day,
        "record_days": record_days,
        "most_liquid": most_liquid,
        "most_dry": most_dry,
        "min_bristol_samples": min_bristol_samples,
    }


def build_stats_raw_debug_text(db: Session, chat_id: int, user_id: int, today: date) -> str:
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

    origin_curr = _current_streak_from_days(origin_days, today)
    origin_best = _best_streak_from_days(origin_days)
    chat_curr = _current_streak_from_days(chat_state_days, today)
    chat_best = _best_streak_from_days(chat_state_days)
    global_curr = _current_streak_from_days(global_state_days, today)
    global_best = _best_streak_from_days(global_state_days)

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
