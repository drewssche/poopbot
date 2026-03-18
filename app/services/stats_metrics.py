from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import PoopEvent
from app.db.models import Session as DaySession
from app.db.models import SessionUserState, User
from app.services.q1_service import mention
from app.services.stats_common import Range


@dataclass(frozen=True)
class ChatPeriodMetrics:
    total_poops: int
    active_participants: int
    active_days_count: int
    period_days: int


def sessions_in_range(db: Session, chat_id: int | None, r: Range) -> list[DaySession]:
    stmt = select(DaySession).where(DaySession.session_date >= r.start, DaySession.session_date <= r.end)
    if chat_id is not None:
        stmt = stmt.where(DaySession.chat_id == chat_id)
    return list(db.scalars(stmt).all())


def bot_start_date(db: Session) -> date | None:
    return db.scalar(select(func.min(DaySession.session_date)))


def chat_start_date(db: Session, chat_id: int) -> date | None:
    return db.scalar(select(func.min(DaySession.session_date)).where(DaySession.chat_id == chat_id))


def chat_origin_events_in_range(
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
    rows = chat_origin_events_in_range(db, chat_id, r, user_id=user_id)
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


def bristol_bucket(bristol: int | None) -> str | None:
    if bristol is None:
        return None
    if bristol <= 2:
        return "🧱"
    if bristol <= 4:
        return "🍌"
    if bristol <= 6:
        return "🍦"
    return "💦"


def bristol_score(bristol: int | None) -> int | None:
    if bristol is None:
        return None
    if bristol <= 2:
        return 1
    if bristol <= 4:
        return 2
    if bristol <= 6:
        return 3
    return 4


def bristol_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    val = max(1, min(4, int(round(avg_score))))
    return {1: "🧱", 2: "🍌", 3: "🍦", 4: "💦"}[val]


def feeling_emoji(feeling: str | None) -> str | None:
    if feeling == "great":
        return "😇"
    if feeling == "ok":
        return "😐"
    if feeling == "bad":
        return "😫"
    return None


def feeling_score(feeling: str | None) -> int | None:
    if feeling == "bad":
        return 1
    if feeling == "ok":
        return 2
    if feeling == "great":
        return 3
    return None


def feeling_from_avg(avg_score: float | None) -> str | None:
    if avg_score is None:
        return None
    val = max(1, min(3, int(round(avg_score))))
    return {1: "😫", 2: "😐", 3: "😇"}[val]


def format_dist_block(title: str, counts: dict[str, int], legend: dict[str, str]) -> list[str]:
    total = sum(counts.values())
    lines = [title]
    if total <= 0:
        lines.append("- нет данных")
        return lines
    for icon, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        pct = int(round((cnt / total) * 100))
        lines.append(f"- {icon} {legend.get(icon, '')}: {pct}% ({cnt})")
    return lines


def display_name(user: User | None, fallback_user_id: int) -> str:
    if user is None:
        return f"id:{fallback_user_id}"
    return mention(user)


def per_user_totals_dedup(db: Session, r: Range) -> dict[int, int]:
    sessions = sessions_in_range(db, None, r)
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


def current_global_king(db: Session, today: date) -> tuple[int, int] | None:
    start = bot_start_date(db)
    if start is None:
        return None
    totals = per_user_totals_dedup(db, Range(start, today))
    if not totals:
        return None
    return sorted(totals.items(), key=lambda x: (-x[1], x[0]))[0]


def is_user_participant_in_chat(db: Session, chat_id: int, user_id: int) -> bool:
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


def collect_events_map(db: Session, session_ids: list[int], user_id: int | None = None) -> dict[tuple[int, int], list[PoopEvent]]:
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


def iter_effective_events(state: SessionUserState, events_map: dict[tuple[int, int], list[PoopEvent]]) -> list[tuple[int | None, str | None]]:
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
