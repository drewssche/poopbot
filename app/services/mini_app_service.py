"""Read-only, chat-local projection for the owner Mini App pilot."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMember, PoopEvent, Session as DaySession
from app.services.event_time_service import event_occurrence_time
from app.services.stats_streaks import best_streak_from_days, current_streak_from_days


def read_view(db: Session, chat_id: int, user_id: int, month: str | None, *, now: datetime | None = None) -> dict:
    chat = db.get(Chat, chat_id)
    if chat is None or db.get(ChatMember, (chat_id, user_id)) is None:
        raise PermissionError("Unavailable scope")
    timezone = ZoneInfo(chat.timezone or "Europe/Minsk")
    today = (now or datetime.now(UTC)).astimezone(timezone).date()
    month = today.strftime("%Y-%m") if month is None else month
    if not isinstance(month, str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise ValueError("Invalid month")
    start = date.fromisoformat(month + "-01")
    if start > today:
        raise ValueError("Future month")
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    filters = (
        DaySession.chat_id == chat_id,
        PoopEvent.origin_chat_id == chat_id,
        PoopEvent.user_id == user_id,
        DaySession.session_date <= today,
    )
    counts = db.execute(
        select(DaySession.session_date, func.count(PoopEvent.id))
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(*filters).group_by(DaySession.session_date).order_by(DaySession.session_date)
    ).all()
    days = [day for day, _ in counts]
    rows = db.execute(
        select(DaySession.session_date, PoopEvent)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(*filters, DaySession.session_date >= start, DaySession.session_date < end)
        .order_by(DaySession.session_date, PoopEvent.event_n, PoopEvent.id)
    ).all()
    events = []
    for day, event in rows:
        occurred = event_occurrence_time(event)
        events.append({
            "id": str(event.id), "date": day.isoformat(),
            "time": occurred.astimezone(timezone).strftime("%H:%M") if occurred else None,
            "kind": (0 if event.bristol <= 2 else 1 if event.bristol <= 4 else 2 if event.bristol <= 6 else 3)
                    if event.bristol is not None else None,
            "feeling": {"great": 0, "ok": 1, "bad": 2}.get(event.feeling),
        })
    return {
        "today": today.isoformat(), "month": month, "timezone": str(timezone),
        "readOnly": True, "events": events,
        "counts": {day.isoformat(): count for day, count in counts},
        "stats": {"total": sum(count for _, count in counts),
                  "best": best_streak_from_days(days), "current": current_streak_from_days(days, today)},
    }
