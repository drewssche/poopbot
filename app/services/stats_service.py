from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Chat, PoopEvent
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
    streak_rows = db.scalars(select(UserStreak).where(UserStreak.chat_id == chat_id)).all()
    if not streak_rows:
        return None

    today_positive = {
        int(uid)
        for uid in db.scalars(
            select(SessionUserState.user_id)
            .join(DaySession, DaySession.session_id == SessionUserState.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == today,
                SessionUserState.poops_n > 0,
            )
        ).all()
    }
    yesterday = today - timedelta(days=1)

    best_user_id = None
    best_streak = 0
    for row in streak_rows:
        projected = int(row.current_streak or 0)
        if int(row.user_id) in today_positive:
            projected = projected + 1 if row.last_poop_date == yesterday else 1
        if projected > best_streak:
            best_streak = projected
            best_user_id = int(row.user_id)

    if best_user_id is None or best_streak <= 0:
        return None
    return db.get(User, best_user_id), best_streak, best_user_id


def build_stats_text_my(db: Session, chat_id: int, user_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)
    first_active_date = None
    if period == "all":
        first_active_date = db.scalar(
            select(func.min(DaySession.session_date))
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                SessionUserState.user_id == user_id,
                SessionUserState.poops_n > 0,
            )
        )
        if first_active_date is not None:
            r = Range(first_active_date, today)
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
    avg_per_day = (float(total_poops) / float(days_total)) if days_total > 0 else 0.0
    avg_per_active_day = (float(total_poops) / float(days_any)) if days_any > 0 else 0.0

    session_date_by_id = {int(s.session_id): s.session_date for s in sessions}
    active_dates = sorted(
        session_date_by_id[int(s.session_id)]
        for s in states
        if int(s.poops_n or 0) > 0 and int(s.session_id) in session_date_by_id
    )
    last_mark_date = active_dates[-1] if active_dates else None
    best_streak_period = 0
    if active_dates:
        run = 1
        best_streak_period = 1
        for i in range(1, len(active_dates)):
            if active_dates[i] == active_dates[i - 1] + timedelta(days=1):
                run += 1
            else:
                run = 1
            if run > best_streak_period:
                best_streak_period = run

    daily_counts: dict[date, int] = {}
    for s in states:
        sid = int(s.session_id)
        if sid not in session_date_by_id:
            continue
        d = session_date_by_id[sid]
        daily_counts[d] = daily_counts.get(d, 0) + int(s.poops_n or 0)
    best_day = None
    if daily_counts:
        best_day = max(daily_counts.items(), key=lambda x: (x[1], x[0]))

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
    streak_val = int(streak.current_streak or 0) if streak else 0
    if last_mark_date == today:
        yesterday = today - timedelta(days=1)
        if streak and streak.last_poop_date == yesterday:
            streak_val = int(streak.current_streak or 0) + 1
        else:
            streak_val = 1

    rank_rows = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .join(DaySession, DaySession.session_id == SessionUserState.session_id)
        .where(
            DaySession.chat_id == chat_id,
            DaySession.session_date >= r.start,
            DaySession.session_date <= r.end,
        )
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc())
    ).all()
    rank = None
    totals = []
    my_total = 0
    for idx, row in enumerate(rank_rows, start=1):
        poops = int(row.poops or 0)
        totals.append(poops)
        if int(row.user_id) == user_id:
            rank = idx
            my_total = poops
    above_pct = _calc_above_percent(my_total, totals) if rank is not None else None

    leader = _chat_streak_leader(db, chat_id, today)

    lines = [
        "🙋‍♂️ Моя статистика",
        f"Период: {_format_period(r)}" + (" (с первого дня активности)" if first_active_date else ""),
        "",
        "Твои итоги:",
        f"- Всего: 💩({total_poops})",
        f"- Дней с 💩: {days_any}/{days_total}",
        f"- Текущий стрик: {streak_val} дн.",
        f"- Лучший стрик за период: {best_streak_period} дн.",
        "",
    ]
    lines.extend(
        [
            "Твоя динамика:",
            f"- В среднем в день: {avg_per_day:.2f}",
            f"- В среднем в активный день: {avg_per_active_day:.2f}",
            f"- Самый активный день: {best_day[0].strftime('%d.%m.%y')} (💩({best_day[1]}))" if best_day else "- Самый активный день: нет данных",
            f"- Последняя отметка: {last_mark_date.strftime('%d.%m.%y')}" if last_mark_date else "- Последняя отметка: нет данных",
            "",
        ]
    )
    if rank is not None:
        lines.extend(
            [
                "Твоя позиция в чате:",
                f"- Место по количеству: #{rank} из {len(rank_rows)}",
                f"- Выше {above_pct}% участников" if above_pct is not None else "- Выше: нет данных",
                "",
            ]
        )

    if leader is not None:
        leader_user, leader_days, leader_user_id = leader
        lines.extend(
            [
                "Лидер стрика в чате:",
                f"- {_streak_nickname(leader_days)} — {_display_name(leader_user, leader_user_id)} ({leader_days} дн.)",
                "",
            ]
        )
    lines.extend(_format_dist_block("Бристоль:", br, BRISTOL_LEGEND))
    lines.append("")
    lines.extend(_format_dist_block("Ощущения:", fe, FEELING_LEGEND))
    return "\n".join(lines)


def build_stats_text_chat(db: Session, chat_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)
    first_active_date = None
    if period == "all":
        first_active_date = db.scalar(
            select(func.min(DaySession.session_date))
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(
                DaySession.chat_id == chat_id,
                SessionUserState.poops_n > 0,
            )
        )
        if first_active_date is not None:
            r = Range(first_active_date, today)

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
    active_participants = sum(1 for row in rows if int(row.poops or 0) > 0)
    avg_per_participant = (float(total_poops) / float(active_participants)) if active_participants > 0 else 0.0

    day_rows = db.execute(
        select(DaySession.session_date, func.sum(SessionUserState.poops_n).label("poops"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.chat_id == chat_id, DaySession.session_id.in_(session_ids))
        .group_by(DaySession.session_date)
        .order_by(DaySession.session_date.asc())
    ).all()
    active_days = [(d, int(p or 0)) for d, p in day_rows if int(p or 0) > 0]
    active_days_count = len(active_days)
    period_days = (r.end - r.start).days + 1
    avg_per_active_day = (float(total_poops) / float(active_days_count)) if active_days_count > 0 else 0.0
    peak_day = max(active_days, key=lambda x: (x[1], x[0])) if active_days else None

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

    top_rows = rows[:5]
    top_user_ids = [int(row.user_id) for row in top_rows]

    streak_rows = db.scalars(select(UserStreak).where(UserStreak.chat_id == chat_id)).all()
    today_positive = {
        int(uid)
        for uid in db.scalars(
            select(SessionUserState.user_id)
            .join(DaySession, DaySession.session_id == SessionUserState.session_id)
            .where(
                DaySession.chat_id == chat_id,
                DaySession.session_date == today,
                SessionUserState.poops_n > 0,
            )
        ).all()
    }
    yesterday = today - timedelta(days=1)
    streak_rank: list[tuple[int, int]] = []
    for row in streak_rows:
        projected = int(row.current_streak or 0)
        if int(row.user_id) in today_positive:
            projected = projected + 1 if row.last_poop_date == yesterday else 1
        if projected > 0:
            streak_rank.append((int(row.user_id), projected))
    streak_rank.sort(key=lambda x: (-x[1], x[0]))
    streak_top3 = streak_rank[:3]

    user_ids = sorted({uid for uid in top_user_ids + [uid for uid, _ in streak_top3]})
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()} if user_ids else {}

    lines = [
        "👥 В этом чате",
        f"Период: {_format_period(r)}" + (" (с первого дня активности)" if first_active_date else ""),
        "",
        "Сводка:",
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
        for idx, row in enumerate(top_rows, start=1):
            user = users.get(int(row.user_id))
            lines.append(f"- {idx}) {_display_name(user, int(row.user_id))} — 💩({int(row.poops or 0)})")
    else:
        lines.append("- пока никто не участвовал")

    lines.append("")
    lines.append("Топ-3 по стрику:")
    if streak_top3:
        for idx, (uid, days) in enumerate(streak_top3, start=1):
            user = users.get(uid)
            lines.append(f"- {idx}) {_streak_nickname(days)} — {_display_name(user, uid)} ({days} дн.)")
    else:
        lines.append("- пока нет активных стриков")

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
    projected_streaks: list[tuple[int, int]] = []
    for row in streak_rows:
        projected = int(row.current_streak or 0)
        if (row.chat_id, row.user_id) in today_positive:
            projected = projected + 1 if row.last_poop_date == yesterday else 1
        if projected > 0:
            projected_streaks.append((int(row.user_id), projected))

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
    top_streaks = sorted(projected_streaks, key=lambda x: (-x[1], x[0]))[:3]
    if not top_streaks:
        lines.append("- пока нет данных")
    else:
        for idx, (_, days) in enumerate(top_streaks, start=1):
            lines.append(f"- #{idx} {_streak_nickname(int(days))} — {int(days)} дн.")

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


def collect_among_chats_snapshot(db: Session, today: date) -> dict:
    min_bristol_samples = 10
    # Exclude private dialogs (chat_id > 0), keep only group/supergroup chats.
    chat_ids = db.scalars(
        select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id < 0)  # noqa: E712
    ).all()
    if not chat_ids:
        return {
            "top_total": [],
            "top_avg": [],
            "top_streak": [],
            "record_day": None,
            "most_liquid": None,
            "most_dry": None,
        }

    sessions = db.scalars(select(DaySession).where(DaySession.chat_id.in_(chat_ids))).all()
    if not sessions:
        return {
            "top_total": [],
            "top_avg": [],
            "top_streak": [],
            "record_day": None,
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
    top_total = sorted(totals, key=lambda x: (-x[1], x[0]))[:5]

    avg_rows: list[tuple[int, float, int, int]] = []
    for chat_id, total in totals:
        participants = participants_map.get(chat_id, 0)
        if participants <= 0:
            continue
        avg_rows.append((chat_id, float(total) / float(participants), total, participants))
    top_avg = sorted(avg_rows, key=lambda x: (-x[1], x[0]))[:5]

    today_positive = set(
        db.execute(
            select(DaySession.chat_id, SessionUserState.user_id)
            .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
            .where(DaySession.session_date == today, SessionUserState.poops_n > 0)
        ).all()
    )
    yesterday = today - timedelta(days=1)
    streak_rows = db.execute(
        select(UserStreak.chat_id, UserStreak.user_id, UserStreak.current_streak, UserStreak.last_poop_date)
        .where(UserStreak.chat_id.in_(chat_ids))
    ).all()
    best_streak_by_chat: dict[int, int] = {}
    for row in streak_rows:
        projected = int(row.current_streak or 0)
        if (row.chat_id, row.user_id) in today_positive:
            projected = projected + 1 if row.last_poop_date == yesterday else 1
        if projected > best_streak_by_chat.get(int(row.chat_id), 0):
            best_streak_by_chat[int(row.chat_id)] = projected
    top_streak = sorted(
        [(chat_id, days) for chat_id, days in best_streak_by_chat.items() if days > 0],
        key=lambda x: (-x[1], x[0]),
    )[:5]

    day_rows = db.execute(
        select(DaySession.chat_id, DaySession.session_date, func.coalesce(func.sum(SessionUserState.poops_n), 0).label("poops"))
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(DaySession.chat_id.in_(chat_ids))
        .group_by(DaySession.chat_id, DaySession.session_date)
    ).all()
    record_day = None
    if day_rows:
        best = max(day_rows, key=lambda r: (int(r.poops or 0), r.session_date, int(r.chat_id)))
        if int(best.poops or 0) > 0:
            record_day = (int(best.chat_id), best.session_date, int(best.poops or 0))

    bristol_rows = db.execute(
        select(DaySession.chat_id, PoopEvent.bristol)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id.in_(chat_ids),
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
        "record_day": record_day,
        "most_liquid": most_liquid,
        "most_dry": most_dry,
        "min_bristol_samples": min_bristol_samples,
    }
