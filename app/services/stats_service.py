from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session

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
        end = start + timedelta(days=6)
        return Range(start, end)
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


def _feeling_emoji(feeling: str | None) -> str | None:
    if feeling == "great":
        return "😇"
    if feeling == "ok":
        return "😐"
    if feeling == "bad":
        return "😫"
    return None


def _bristol_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    val = max(1, min(4, int(round(avg_score))))
    return {1: "🧱", 2: "🍌", 3: "🍦", 4: "💦"}[val]


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
    days_any = sum(1 for s in states if (s.poops_n or 0) > 0)
    days_total = (r.end - r.start).days + 1

    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for s in states:
        if (s.poops_n or 0) <= 0:
            continue
        b = _bristol_bucket(s.bristol)
        if b:
            br[b] += 1
        f = _feeling_emoji(s.feeling)
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

    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for s in states_pos:
        b = _bristol_bucket(s.bristol)
        if b:
            br[b] += 1
        f = _feeling_emoji(s.feeling)
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
    _ = period  # В глобальной статистике период всегда за все время.
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

    states_pos = db.scalars(
        select(SessionUserState).where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
        )
    ).all()

    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}
    for s in states_pos:
        b = _bristol_bucket(s.bristol)
        if b:
            br[b] += 1
        f = _feeling_emoji(s.feeling)
        if f:
            fe[f] += 1

    br_score_case = case(
        (SessionUserState.bristol <= 2, 1),
        (SessionUserState.bristol <= 4, 2),
        (SessionUserState.bristol <= 6, 3),
        else_=4,
    )
    fe_score_case = case(
        (SessionUserState.feeling == "bad", 1),
        (SessionUserState.feeling == "ok", 2),
        (SessionUserState.feeling == "great", 3),
        else_=None,
    )

    br_rows = db.execute(
        select(SessionUserState.user_id, func.avg(cast(br_score_case, Float)).label("avg_br"))
        .where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
            SessionUserState.bristol.isnot(None),
        )
        .group_by(SessionUserState.user_id)
    ).all()

    fe_rows = db.execute(
        select(SessionUserState.user_id, func.avg(cast(fe_score_case, Float)).label("avg_fe"))
        .where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
            SessionUserState.feeling.isnot(None),
        )
        .group_by(SessionUserState.user_id)
    ).all()

    br_map = {int(row.user_id): float(row.avg_br) for row in br_rows if row.avg_br is not None}
    fe_map = {int(row.user_id): float(row.avg_fe) for row in fe_rows if row.avg_fe is not None}

    my_br_avg = br_map.get(user_id)
    my_fe_avg = fe_map.get(user_id)
    my_br_icon = _bristol_from_avg(my_br_avg)
    my_fe_icon = _feeling_from_avg(my_fe_avg)

    my_br_pct = _calc_above_percent(int(round(my_br_avg * 1000)), [int(round(v * 1000)) for v in br_map.values()]) if my_br_avg is not None else None
    my_fe_pct = _calc_above_percent(int(round(my_fe_avg * 1000)), [int(round(v * 1000)) for v in fe_map.values()]) if my_fe_avg is not None else None

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
