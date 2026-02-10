from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select, func, case, cast, Float
from sqlalchemy.orm import Session

from app.db.models import Session as DaySession, SessionUserState, User, UserStreak
from app.services.q1_service import mention


@dataclass(frozen=True)
class Range:
    start: date
    end: date  # inclusive


def period_to_range(today: date, period: str) -> Range:
    if period == "today":
        return Range(today, today)
    if period == "week":
        return Range(today - timedelta(days=6), today)
    if period == "month":
        return Range(today - timedelta(days=29), today)
    if period == "year":
        return Range(today - timedelta(days=364), today)
    return Range(date(1970, 1, 1), today)


def _sessions_in_range(db: Session, chat_id: int | None, r: Range) -> list[DaySession]:
    stmt = select(DaySession).where(DaySession.session_date >= r.start, DaySession.session_date <= r.end)
    if chat_id is not None:
        stmt = stmt.where(DaySession.chat_id == chat_id)
    return list(db.scalars(stmt).all())


def _dist_percent(counts: dict[str, int], legend: dict[str, str] | None = None) -> str:
    total = sum(counts.values())
    if total <= 0:
        return "—"
    parts = []
    for k, v in counts.items():
        pct = int(round((v / total) * 100))
        if legend and k in legend:
            parts.append(f"{k} {legend[k]}: {pct}%")
        else:
            parts.append(f"{k}: {pct}%")
    return " | ".join(parts)


def _bristol_bucket(val: int | None) -> str | None:
    if val is None:
        return None
    if val <= 2:
        return "🧱"
    if val <= 4:
        return "🍌"
    if val <= 6:
        return "🍦"
    return "💦"


def _bristol_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    s = int(round(avg_score))
    s = max(1, min(4, s))
    return {1: "🧱", 2: "🍌", 3: "🍦", 4: "💦"}[s]


def _feeling_emoji(val: str | None) -> str | None:
    if val == "great":
        return "😇"
    if val == "ok":
        return "😐"
    if val == "bad":
        return "😫"
    return None


def _feeling_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    s = int(round(avg_score))
    s = max(1, min(3, s))
    return {3: "😇", 2: "😐", 1: "😫"}[s]


def _percentile(value: float | None, values: list[float]) -> int | None:
    if value is None or not values:
        return None
    less = sum(1 for v in values if v < value)
    eq = sum(1 for v in values if v == value)
    n = len(values)
    pct = int(round(100.0 * (less + 0.5 * eq) / n))
    return max(0, min(100, pct))


BRISTOL_LEGEND = {
    "🧱": "(жёстко/сухо)",
    "🍌": "(норма)",
    "🍦": "(мягко)",
    "💦": "(водичка)",
}

FEELING_LEGEND = {
    "😇": "(прекрасно)",
    "😐": "(сойдёт)",
    "😫": "(ужасно)",
}


def build_stats_text_my(db: Session, chat_id: int, user_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)
    sessions = _sessions_in_range(db, chat_id, r)
    if not sessions:
        return f"🙋‍♂️ Статистика\nПериод: {r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]

    states = db.scalars(
        select(SessionUserState).where(SessionUserState.session_id.in_(session_ids), SessionUserState.user_id == user_id)
    ).all()

    total_poops = sum(s.poops_n for s in states)
    days_any = sum(1 for s in states if s.poops_n > 0)
    days_total = (r.end - r.start).days + 1

    br = {"🧱": 0, "🍌": 0, "🍦": 0, "💦": 0}
    fe = {"😇": 0, "😐": 0, "😫": 0}

    for s in states:
        if s.poops_n <= 0:
            continue
        b = _bristol_bucket(s.bristol)
        if b:
            br[b] += 1
        f = _feeling_emoji(s.feeling)
        if f:
            fe[f] += 1

    streak = db.get(UserStreak, {"chat_id": chat_id, "user_id": user_id})
    streak_val = streak.current_streak if streak else 0

    return (
        f"🙋‍♂️ Моя статистика\n"
        f"Период: {r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}\n\n"
        f"💩 Всего: {total_poops}\n"
        f"📌 Дней с 💩: {days_any}/{days_total}\n"
        f"🔥 Текущий стрик: {streak_val} дн.\n\n"
        f"🧻 Бристоль: {_dist_percent(br, BRISTOL_LEGEND)}\n"
        f"😮‍💨 Ощущения: {_dist_percent(fe, FEELING_LEGEND)}"
    )


def build_stats_text_chat(db: Session, chat_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)
    sessions = _sessions_in_range(db, chat_id, r)
    if not sessions:
        return f"👥 Статистика чата\nПериод: {r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]

    rows = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .where(SessionUserState.session_id.in_(session_ids))
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc())
    ).all()

    total_poops = sum(int(rw.poops or 0) for rw in rows)

    states_pos = db.scalars(
        select(SessionUserState).where(SessionUserState.session_id.in_(session_ids), SessionUserState.poops_n > 0)
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

    user_ids = [int(rw.user_id) for rw in rows]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()}

    lines = [
        "👥 В этом чате",
        f"Период: {r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}",
        "",
        f"💩 Всего по чату: {total_poops}",
        f"🧻 Бристоль: {_dist_percent(br, BRISTOL_LEGEND)}",
        f"😮‍💨 Ощущения: {_dist_percent(fe, FEELING_LEGEND)}",
        "",
        "Топ участников:",
    ]

    rank = 1
    for rw in rows[:10]:
        u = users.get(int(rw.user_id))
        if not u:
            continue
        lines.append(f"{rank}) {mention(u)} — 💩({int(rw.poops or 0)})")
        rank += 1

    if rank == 1:
        lines.append("(Пока никто не участвовал)")

    return "\n".join(lines)


def build_stats_text_global(db: Session, user_id: int, today: date, period: str) -> str:
    r = period_to_range(today, period)

    sessions = _sessions_in_range(db, None, r)
    if not sessions:
        return f"🌍 Глобальная статистика\nПериод: {r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}\n\nПока пусто."

    session_ids = [s.session_id for s in sessions]

    users_count = db.scalar(
        select(func.count(func.distinct(SessionUserState.user_id))).where(SessionUserState.session_id.in_(session_ids))
    ) or 0

    total_poops = db.scalar(
        select(func.coalesce(func.sum(SessionUserState.poops_n), 0)).where(SessionUserState.session_id.in_(session_ids))
    ) or 0

    king_row = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .where(SessionUserState.session_id.in_(session_ids))
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc())
        .limit(1)
    ).first()
    king_poops = int(king_row.poops) if king_row else 0
    king_user_id = int(king_row.user_id) if king_row else None

    agg = db.execute(
        select(SessionUserState.user_id, func.sum(SessionUserState.poops_n).label("poops"))
        .where(SessionUserState.session_id.in_(session_ids))
        .group_by(SessionUserState.user_id)
        .order_by(func.sum(SessionUserState.poops_n).desc())
    ).all()

    user_total = 0
    rank = None
    for i, row in enumerate(agg, start=1):
        if int(row.user_id) == user_id:
            rank = i
            user_total = int(row.poops or 0)
            break

    states_pos = db.scalars(
        select(SessionUserState).where(SessionUserState.session_id.in_(session_ids), SessionUserState.poops_n > 0)
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
        select(
            SessionUserState.user_id,
            func.avg(cast(br_score_case, Float)).label("avg_br"),
        )
        .where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
            SessionUserState.bristol.isnot(None),
        )
        .group_by(SessionUserState.user_id)
    ).all()

    fe_rows = db.execute(
        select(
            SessionUserState.user_id,
            func.avg(cast(fe_score_case, Float)).label("avg_fe"),
        )
        .where(
            SessionUserState.session_id.in_(session_ids),
            SessionUserState.poops_n > 0,
            SessionUserState.feeling.isnot(None),
        )
        .group_by(SessionUserState.user_id)
    ).all()

    br_map = {int(rw.user_id): float(rw.avg_br) for rw in br_rows if rw.avg_br is not None}
    fe_map = {int(rw.user_id): float(rw.avg_fe) for rw in fe_rows if rw.avg_fe is not None}

    br_values = list(br_map.values())
    fe_values = list(fe_map.values())

    my_br_avg = br_map.get(user_id)
    my_fe_avg = fe_map.get(user_id)

    my_br_pct = _percentile(my_br_avg, br_values)
    my_fe_pct = _percentile(my_fe_avg, fe_values)

    my_br_icon = _bristol_from_avg(my_br_avg)
    my_fe_icon = _feeling_from_avg(my_fe_avg)

    lines = [
        "🌍 Глобальная статистика",
        f"Период: {r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}",
        "",
        f"👤 Участников: {int(users_count)}",
        f"💩 Всего: {int(total_poops)}",
        f"👑 Король какашек — {int(king_poops)}",
        f"🧻 Бристоль: {_dist_percent(br, BRISTOL_LEGEND)}",
        f"😮‍💨 Ощущения: {_dist_percent(fe, FEELING_LEGEND)}",
        "",
    ]

    if rank is None:
        lines.append("Твоё место по 💩: пока нет данных.")
    else:
        lines.append(f"Твоё место по 💩: #{rank} из {len(agg)} (💩={user_total})")
        if king_user_id is not None and user_id == king_user_id:
            lines.append("Вы — король какашек, топ-1!")

    if my_br_pct is None:
        lines.append("Твой Бристоль: — (процентиль: —)")
    else:
        icon = my_br_icon or "🧻"
        expl = BRISTOL_LEGEND.get(icon, "")
        lines.append(f"Твой Бристоль: {icon} {expl} (процентиль: {my_br_pct}%)")

    if my_fe_pct is None:
        lines.append("Твои ощущения: — (процентиль: —)")
    else:
        icon = my_fe_icon or "😮‍💨"
        expl = FEELING_LEGEND.get(icon, "")
        lines.append(f"Твои ощущения: {icon} {expl} (процентиль: {my_fe_pct}%)")

    return "\n".join(lines)
