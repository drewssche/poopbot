from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Chat, PoopEvent
from app.db.models import Session as DaySession
from app.db.models import SessionUserState
from app.services.stats_common import Range, estimate_waste_metrics
from app.services.time_service import now_in_tz


def visible_group_chat_ids(db: Session) -> list[int]:
    return db.scalars(
        select(Chat.chat_id).where(Chat.is_enabled == True, Chat.show_in_global == True, Chat.chat_id < 0)  # noqa: E712
    ).all()


def rank_chat_among_groups_by_total(db: Session, chat_id: int, r: Range) -> tuple[int | None, int]:
    chat_ids = visible_group_chat_ids(db)
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
    from app.services.stats_streaks import compute_chat_user_streaks_live_per_chat_today

    min_bristol_samples = 10
    chat_ids = visible_group_chat_ids(db)
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

    today_by_chat: dict[int, date] = {}
    for cid in chat_ids:
        if cid in latest_session_date_by_chat:
            today_by_chat[cid] = latest_session_date_by_chat[cid]
            continue
        today_by_chat[cid] = now_in_tz(tz_by_chat.get(cid, "Europe/Minsk")).date()
    streaks_live = compute_chat_user_streaks_live_per_chat_today(db, chat_ids, today_by_chat)
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
