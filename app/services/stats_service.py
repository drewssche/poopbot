from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import PoopEvent
from app.db.models import Session as DaySession
from app.db.models import SessionUserState, User, UserStreak
from app.services.q1_service import mention


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


def _sessions_in_range(db: Session, chat_id: int | None, r: Range) -> list[DaySession]:
    stmt = select(DaySession).where(DaySession.session_date >= r.start, DaySession.session_date <= r.end)
    if chat_id is not None:
        stmt = stmt.where(DaySession.chat_id == chat_id)
    return list(db.scalars(stmt).all())


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


def _calc_above_percent(value: int, all_values: list[int]) -> int | None:
    if not all_values:
        return None
    less = sum(1 for v in all_values if v < value)
    eq = sum(1 for v in all_values if v == value)
    return int(round(100.0 * (less + 0.5 * eq) / len(all_values)))


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


def build_stats_text_my(db: Session, chat_id: int, user_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)
    sessions = _sessions_in_range(db, chat_id, r)
    if not sessions:
        return f"🙋‍♂️ Моя статистика\nПериод: {_format_period(r)}\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]
    states = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.user_id == user_id,
        )
    ).all()

    total_poops = sum(int(s.poops_n or 0) for s in states)
    days_any = sum(1 for s in states if int(s.poops_n or 0) > 0)
    days_total = (r.end - r.start).days + 1

    events_map = _collect_events_map(db, session_ids, user_id=user_id)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for s in states:
        for bristol, feeling in _iter_effective_events(s, events_map):
            b = _bristol_bucket(bristol)
            if b:
                br[b] += 1
            f = _feeling_emoji(feeling)
            if f:
                fe[f] += 1

    streak = db.get(UserStreak, {"chat_id": chat_id, "user_id": user_id})
    streak_val = int(streak.current_streak) if streak else 0

    lines = [
        "🙋‍♂️ Моя статистика",
        f"Период: {_format_period(r)}",
        "",
        "Итоги:",
        f"- Всего: 💩({total_poops})",
        f"- Дней с 💩: {days_any}/{days_total}",
        f"- Текущий стрик: {streak_val} дн.",
        "",
    ]
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    return "\n".join(lines)


def build_stats_text_chat(db: Session, chat_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)
    sessions = _sessions_in_range(db, chat_id, r)
    if not sessions:
        return f"👥 В этом чате\nПериод: {_format_period(r)}\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]

    rows = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .where(SessionUserState.session_id.in_(session_ids))
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc())
    ).all()
    total_poops = sum(int(row.poops or 0) for row in rows)

    states_pos = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
        )
    ).all()

    events_map = _collect_events_map(db, session_ids)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for s in states_pos:
        for bristol, feeling in _iter_effective_events(s, events_map):
            b = _bristol_bucket(bristol)
            if b:
                br[b] += 1
            f = _feeling_emoji(feeling)
            if f:
                fe[f] += 1

    user_ids = [int(row.user_id) for row in rows]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()}

    lines = [
        "👥 В этом чате",
        f"Период: {_format_period(r)}",
        "",
        "Итоги:",
        f"- Всего: 💩({total_poops})",
        "",
        "Топ участников:",
    ]

    if rows:
        for idx, row in enumerate(rows[:10], start=1):
            user = users.get(int(row.user_id))
            lines.append(f"- {idx}) {_display_name(user, int(row.user_id))} — 💩({int(row.poops or 0)})")
    else:
        lines.append("- пока никто не участвовал")

    lines.append("")
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    return "\n".join(lines)


def build_stats_text_global(db: Session, user_id: int, today: date, period: str) -> str:
    _ = period
    all_time = Range(date(1970, 1, 1), today)

    sessions = _sessions_in_range(db, None, all_time)
    if not sessions:
        return "🌍 Глобальная статистика\nПериод: за все время\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]

    users_count = db.scalar(
        select(func.count(func.distinct(SessionUserState.user_id))).where(SessionUserState.session_id.in_(session_ids))
    ) or 0

    total_poops = db.scalar(
        select(func.coalesce(func.sum(SessionUserState.poops_n), 0)).where(SessionUserState.session_id.in_(session_ids))
    ) or 0

    agg = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .where(SessionUserState.session_id.in_(session_ids))
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc())
    ).all()

    my_total = 0
    my_rank = None
    totals: list[int] = []
    for idx, row in enumerate(agg, start=1):
        poops = int(row.poops or 0)
        totals.append(poops)
        if int(row.user_id) == user_id:
            my_rank = idx
            my_total = poops

    above_pct = _calc_above_percent(my_total, totals) if my_rank is not None else None
    top5 = [(TOP5_ROLES[i], int(row.poops or 0)) for i, row in enumerate(agg[:5])]

    streak_rows = db.execute(
        select(UserStreak.chat_id, UserStreak.user_id, UserStreak.current_streak, UserStreak.last_poop_date)
    ).all()
    today_positive = set(
        db.execute(
            select(DaySession.chat_id, SessionUserState.user_id)
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(DaySession.session_date == today, SessionUserState.poops_n > 0)
        ).all()
    )
    yesterday = today - timedelta(days=1)
    max_streak = 0
    for row in streak_rows:
        projected = int(row.current_streak or 0)
        if (row.chat_id, row.user_id) in today_positive:
            projected = projected + 1 if row.last_poop_date == yesterday else 1
        max_streak = max(max_streak, projected)

    states_pos = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
        )
    ).all()

    events_map = _collect_events_map(db, session_ids)
    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    user_br_scores: dict[int, list[int]] = {}
    user_fe_scores: dict[int, list[int]] = {}

    for s in states_pos:
        uid = int(s.user_id)
        for bristol, feeling in _iter_effective_events(s, events_map):
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
        "Период: за все время",
        "",
        "Итоги:",
        f"- Участников: {int(users_count)}",
        f"- Всего: 💩({int(total_poops)})",
        "",
        "Топ-5:",
    ]

    if top5:
        for role, poops in top5:
            lines.append(f"- {role} — 💩({poops})")
    else:
        lines.append("- пока нет данных")

    lines.extend(["", "Легенда стрика:"])
    if max_streak <= 0:
        lines.append("- пока нет данных")
    else:
        lines.append(f"- Железный кишечник — {int(max_streak)} дн.")

    lines.extend(["", "Твое место в топе:", f"- {me_name}"])
    if my_rank is None:
        lines.append("- Пока нет данных за все время")
    else:
        lines.append(f"- Место: #{my_rank} из {len(agg)}")
        lines.append(f"- Всего: 💩({my_total})")
        if above_pct is not None:
            lines.append(f"- Выше {above_pct}% участников")

    lines.append("")
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))

    lines.extend(["", "Твое качество:"])
    if my_br_pct is None or my_br_icon is None:
        lines.append("- Бристоль: нет данных")
    else:
        lines.append(f"- Бристоль: {my_br_icon} (выше {my_br_pct}%)")

    if my_fe_pct is None or my_fe_icon is None:
        lines.append("- Ощущения: нет данных")
    else:
        lines.append(f"- Ощущения: {my_fe_icon} (выше {my_fe_pct}%)")

    return "\n".join(lines)
