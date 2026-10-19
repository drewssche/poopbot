"""Chat-local Mini App projection and transactional mark creation."""
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
        "readOnly": False, "scope": "personal" if chat_id > 0 else "group", "events": events,
        "counts": {day.isoformat(): count for day, count in counts},
        "stats": {"total": sum(count for _, count in counts),
                  "best": best_streak_from_days(days), "current": current_streak_from_days(days, today)},
    }


def prepare_scope(db: Session, chat_id: int, user: dict) -> None:
    """Called only after signed identity and current group membership validation."""
    from app.services.repo_service import ensure_chat_member, upsert_user
    if chat_id > 0 and chat_id != user['id']:
        raise PermissionError('Unavailable personal scope')
    chat = db.get(Chat, chat_id)
    if chat is None:
        if chat_id != user['id']:
            raise PermissionError('Unavailable group')
        chat = Chat(chat_id=chat_id, notifications_enabled=False, late_reminder_enabled=False)
        db.add(chat)
    elif chat_id < 0 and not chat.is_enabled:
        raise PermissionError('Unavailable group')
    upsert_user(db, user_id=user['id'], username=user.get('username'),
                first_name=user.get('first_name'), last_name=user.get('last_name'))
    db.flush()
    ensure_chat_member(db, chat_id, user['id'])
    db.flush()


class MarkRejected(ValueError):
    pass


def record_mark(db: Session, chat_id: int, user_id: int, body: dict, *, now: datetime | None = None) -> dict:
    """Atomic mark + receipt. Caller commits before any Telegram UI operation."""
    import hashlib
    import json
    from uuid import UUID
    from app.db.models import MiniAppRequest, SessionUserState, User
    from app.services.repo_service import get_or_create_session
    from app.services.q1_service import apply_plus

    if set(body) != {'requestId', 'date', 'kind', 'feeling'}:
        raise ValueError('Invalid mark')
    try:
        request_id = str(UUID(body['requestId']))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid request ID') from None
    if (type(body['kind']) is not int or body['kind'] not in range(4)
            or type(body['feeling']) is not int or body['feeling'] not in range(3)
            or not isinstance(body['date'], str)):
        raise ValueError('Invalid mark')
    payload = hashlib.sha256(json.dumps([chat_id, body['date'], body['kind'], body['feeling']]).encode()).hexdigest()
    # Shared with Q1 +/- so concurrent bot/app actions cannot reuse event_n.
    db.scalar(select(User).where(User.user_id == user_id).with_for_update())
    receipt = db.get(MiniAppRequest, (user_id, request_id))
    if receipt:
        if receipt.payload_hash != payload:
            raise MarkRejected('Этот запрос уже использован для другой отметки.')
        return {'eventId': str(receipt.event_id) if receipt.event_id else None, 'repeated': True}
    chat = db.get(Chat, chat_id)
    if chat is None or db.get(ChatMember, (chat_id, user_id)) is None:
        raise PermissionError('Unavailable scope')
    local_now = (now or datetime.now(UTC)).astimezone(ZoneInfo(chat.timezone))
    if body['date'] != local_now.date().isoformat():
        raise MarkRejected('Добавить отметку можно только за сегодня. Для прошлых дней используй восстановление в боте.')
    if local_now.hour * 60 + local_now.minute < 5 or local_now.hour * 60 + local_now.minute >= 1435:
        raise MarkRejected('Новая сессия начнётся в 00:05.')
    sess = get_or_create_session(db, chat_id, local_now.date())
    db.refresh(sess, with_for_update=True)
    if sess.status != 'active':
        raise MarkRejected('Сессия закрыта.')
    rows = db.scalars(select(PoopEvent).where(PoopEvent.session_id == sess.session_id,
                                             PoopEvent.user_id == user_id).order_by(PoopEvent.event_n)).all()
    state = db.get(SessionUserState, (sess.session_id, user_id))
    # Do not run legacy normalization on ambiguous data through this new path.
    if (any(e.origin_chat_id != chat_id for e in rows)
            or [e.event_n for e in rows] != list(range(1, len(rows) + 1))
            or (int(state.poops_n or 0) if state else 0) != len(rows)):
        raise MarkRejected('Данные этой сессии требуют проверки. Обратись к владельцу бота.')
    changed, _ = apply_plus(db, sess.session_id, user_id, origin_chat_id=chat_id)
    if not changed:
        raise MarkRejected('Достигнут лимит: 10 отметок за день.')
    db.flush()
    state = db.get(SessionUserState, (sess.session_id, user_id))
    event = db.scalar(select(PoopEvent).where(PoopEvent.session_id == sess.session_id,
                                            PoopEvent.user_id == user_id, PoopEvent.event_n == state.poops_n))
    event.bristol = (2, 4, 6, 7)[body['kind']]
    event.feeling = ('great', 'ok', 'bad')[body['feeling']]
    event.occurred_at = local_now.astimezone(UTC)
    state.bristol, state.feeling = event.bristol, event.feeling
    db.add(MiniAppRequest(user_id=user_id, request_id=request_id, payload_hash=payload, event_id=event.id))
    db.flush()
    return {'eventId': str(event.id), 'repeated': False}
