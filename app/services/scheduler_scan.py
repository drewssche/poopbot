"""Read a fresh, bounded snapshot to skip chats with no scheduled work.

The snapshot is only a filter. Actions still reload their chat and markers in
their own transaction, so a concurrent user action cannot cause a stale send.
Nothing is cached between ticks; unfamiliar/boundary cases use the full worker.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from pytz import UnknownTimeZoneError
from sqlalchemy import and_, or_, select

from app.db.models import Chat, CommandMessage, Session as DaySession, SessionMessage
from app.services.scheduler_reports import periodic_reports_due
from app.services.time_service import now_in_tz


@dataclass(frozen=True)
class ChatSchedule:
    chat_id: int
    timezone: str
    post_time: time
    notifications_enabled: bool


@dataclass(frozen=True)
class SessionSchedule:
    status: str
    q1_message_id: int | None


@dataclass
class SchedulerSnapshot:
    chats: list[ChatSchedule]
    dates: dict[int, date | None]
    sessions: dict[tuple[int, date], SessionSchedule]
    active_dates: dict[int, list[date]]
    sent_reports: set[tuple[int, str, date]]

    def needs_processing(self, chat: ChatSchedule, now: datetime) -> bool:
        today = now.date()
        # Leave midnight maintenance, reminders, closure and holiday behaviour
        # to the existing worker. Recheck time after any preceding slow chat.
        if (today != self.dates[chat.chat_id]
                or now.time() < time(0, 11)
                or now.time() >= time(23, 30)
                or (today.month, today.day) in {(2, 9), (11, 19)}):
            return True
        if any(d < today for d in self.active_dates.get(chat.chat_id, ())):
            return True
        session = self.sessions.get((chat.chat_id, today))
        if session is None:
            return True
        if session.status == "closed" or not chat.notifications_enabled:
            return False
        now_min = now.hour * 60 + now.minute
        post_min = chat.post_time.hour * 60 + chat.post_time.minute
        if now_min >= post_min and not session.q1_message_id:
            return True
        if now_min >= 9 * 60:
            report_date = today - timedelta(days=1)
            return any(
                (chat.chat_id, kind, report_date) not in self.sent_reports
                for kind, _, _ in periodic_reports_due(report_date)
            )
        return False


def load_scheduler_snapshot(db) -> SchedulerSnapshot:
    chats = [ChatSchedule(*row) for row in db.execute(select(
        Chat.chat_id, Chat.timezone, Chat.post_time, Chat.notifications_enabled,
    ).where(Chat.is_enabled == True))]
    dates = {}
    for chat in chats:
        try:
            dates[chat.chat_id] = now_in_tz(chat.timezone).date()
        except UnknownTimeZoneError:
            # The worker logs this chat's error without blocking other chats.
            dates[chat.chat_id] = None
    snapshot = SchedulerSnapshot(chats, dates, {}, {}, set())
    if not chats:
        return snapshot
    chat_ids = list(dates)
    local_dates = {d for d in dates.values() if d is not None}
    rows = db.execute(select(
        DaySession.chat_id, DaySession.session_date, DaySession.status,
        SessionMessage.message_id,
    ).outerjoin(SessionMessage, and_(
        SessionMessage.session_id == DaySession.session_id,
        SessionMessage.kind == "Q1",
    )).where(
        DaySession.chat_id.in_(chat_ids),
        or_(DaySession.status == "active", DaySession.session_date.in_(local_dates)),
    ))
    for chat_id, session_date, status, q1_id in rows:
        snapshot.sessions[chat_id, session_date] = SessionSchedule(status, q1_id)
        if status == "active":
            snapshot.active_dates.setdefault(chat_id, []).append(session_date)
    report_dates = {d - timedelta(days=1) for d in local_dates
                    if periodic_reports_due(d - timedelta(days=1))}
    if report_dates:
        snapshot.sent_reports = set(db.execute(select(
            CommandMessage.chat_id, CommandMessage.command, CommandMessage.session_date,
        ).where(
            CommandMessage.chat_id.in_(chat_ids), CommandMessage.user_id == 0,
            CommandMessage.command.in_(("weekly_stats", "monthly_stats", "yearly_stats")),
            CommandMessage.session_date.in_(report_dates),
        )).tuples())
    return snapshot
