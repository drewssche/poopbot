from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db.models import Chat, PoopEvent, Session as DaySession, SessionUserState
from app.services.app_setting_service import get_setting, set_setting
from app.services.poop_event_service import create_event, delete_event
from app.services.q1_service import _refresh_user_streak_cache_before_current_day

RESTORE_PERIOD_KEY = "restore_period"
MAX_RESTORE_DAYS = 30


@dataclass(frozen=True)
class RestorePeriod:
    start: date
    end: date

    def __post_init__(self):
        if not 1 <= self.days <= MAX_RESTORE_DAYS:
            raise ValueError("Период должен быть от 1 до 30 дней")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.end.strftime("%d.%m.%Y")
        return f"{self.start:%d.%m.%Y}–{self.end:%d.%m.%Y}"

    @property
    def marker(self) -> str:
        return f"__restore_period__:{self.start}:{self.end}"


def get_restore_period(db) -> RestorePeriod | None:
    raw = get_setting(db, RESTORE_PERIOD_KEY)
    if not raw:
        return None
    data = json.loads(raw)
    return RestorePeriod(date.fromisoformat(data["start"]), date.fromisoformat(data["end"]))


def choose_restore_period(db, *, today: date, days: int) -> RestorePeriod:
    if not 1 <= days <= MAX_RESTORE_DAYS:
        raise ValueError("Выбери от 1 до 30 дней")
    period = RestorePeriod(today - timedelta(days=days), today - timedelta(days=1))
    set_setting(db, RESTORE_PERIOD_KEY, json.dumps({"start": period.start.isoformat(), "end": period.end.isoformat()}))
    db.flush()
    return period


def ensure_restore_period(db, *, today: date) -> RestorePeriod:
    return get_restore_period(db) or choose_restore_period(db, today=today, days=1)


def lock_restore_chat(db, chat_id: int) -> None:
    # Serializes claims for a chat, including creation of missing historical sessions.
    db.scalar(select(Chat).where(Chat.chat_id == chat_id).with_for_update())


def add_missing_mark(db, *, sess: DaySession, user_id: int, marker: str | None = None) -> bool:
    state = db.get(SessionUserState, (sess.session_id, user_id))
    existing = db.scalar(select(PoopEvent.id).where(
        PoopEvent.session_id == sess.session_id, PoopEvent.user_id == user_id,
        PoopEvent.origin_chat_id == sess.chat_id,
    ).limit(1))
    if existing is not None or (state is not None and state.poops_n > 0):
        return False
    if state is None:
        state = SessionUserState(session_id=sess.session_id, user_id=user_id)
        db.add(state)
    state.poops_n = 1
    state.achievement_text = marker
    state.bristol = None
    state.feeling = None
    create_event(db, sess.session_id, user_id, 1, origin_chat_id=sess.chat_id,
                 source="restore" if marker else "late")
    db.flush()
    return True


def restore_period(db, *, chat_id: int, user_id: int, period: RestorePeriod, today: date) -> tuple[bool, str]:
    if period.end >= today:
        return False, "Выбранный период ещё не завершён в этом чате"
    count = 0
    for offset in range(period.days):
        day = period.start + timedelta(days=offset)
        sess = db.scalar(select(DaySession).where(DaySession.chat_id == chat_id, DaySession.session_date == day))
        if sess is None:
            sess = DaySession(chat_id=chat_id, session_date=day, status="closed", end_at=datetime.now(UTC))
            db.add(sess)
            db.flush()
        count += add_missing_mark(db, sess=sess, user_id=user_id, marker=period.marker)
    _refresh_user_streak_cache_before_current_day(db, chat_id=chat_id, user_id=user_id, current_session_date=today)
    return bool(count), (f"Добавлено отметок: {count}. Период: {period.label}" if count else f"За {period.label} пропусков нет")


def undo_period(db, *, chat_id: int, user_id: int, period: RestorePeriod, today: date) -> tuple[bool, str]:
    rows = db.execute(select(DaySession, SessionUserState).join(SessionUserState).where(
        DaySession.chat_id == chat_id, DaySession.session_date.between(period.start, period.end),
        SessionUserState.user_id == user_id, SessionUserState.achievement_text == period.marker,
        SessionUserState.poops_n == 1,
    )).all()
    count = 0
    for sess, state in rows:
        events = db.scalars(select(PoopEvent).where(PoopEvent.session_id == sess.session_id, PoopEvent.user_id == user_id)).all()
        # Do not remove a subsequently edited or supplemented mark.
        if len(events) != 1 or events[0].event_n != 1 or events[0].bristol is not None or events[0].feeling is not None:
            continue
        delete_event(db, sess.session_id, user_id, 1)
        state.poops_n = 0
        state.achievement_text = None
        count += 1
    db.flush()
    _refresh_user_streak_cache_before_current_day(db, chat_id=chat_id, user_id=user_id, current_session_date=today)
    return bool(count), f"Отменено отметок: {count}. Период: {period.label}"


def can_mark_closed_session(sess: DaySession, today: date) -> bool:
    return sess.status == "closed" and sess.session_date <= today <= sess.session_date + timedelta(days=1)


def mark_forgotten_day(db, *, sess: DaySession, user_id: int, today: date) -> tuple[bool, str]:
    if not can_mark_closed_session(sess, today):
        return False, "Срок истёк: отметиться можно до конца следующего дня"
    if not add_missing_mark(db, sess=sess, user_id=user_id):
        return False, "За этот день ты уже отметился"
    _refresh_user_streak_cache_before_current_day(
        db, chat_id=sess.chat_id, user_id=user_id,
        current_session_date=max(today, sess.session_date + timedelta(days=1)),
    )
    return True, f"Отметка за {sess.session_date:%d.%m.%Y} сохранена"
