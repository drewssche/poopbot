from __future__ import annotations

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import PoopEvent, SessionUserState


def list_events(db: Session, session_id: int, user_id: int) -> list[PoopEvent]:
    return db.scalars(
        select(PoopEvent)
        .where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id)
        .order_by(PoopEvent.event_n.asc())
    ).all()


def list_origin_events(db: Session, session_id: int, user_id: int, origin_chat_id: int) -> list[PoopEvent]:
    return db.scalars(
        select(PoopEvent)
        .where(
            PoopEvent.session_id == session_id,
            PoopEvent.user_id == user_id,
            PoopEvent.origin_chat_id == origin_chat_id,
        )
        .order_by(PoopEvent.event_n.asc(), PoopEvent.id.asc())
    ).all()


def count_origin_events(db: Session, session_id: int, user_id: int, origin_chat_id: int) -> int:
    return len(list_origin_events(db, session_id, user_id, origin_chat_id))


def normalize_session_user_state_to_origin_chat(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    origin_chat_id: int,
) -> int:
    events = list_events(db, session_id, user_id)
    local_events = [ev for ev in events if int(ev.origin_chat_id) == int(origin_chat_id)]
    foreign_events = [ev for ev in events if int(ev.origin_chat_id) != int(origin_chat_id)]

    for ev in foreign_events:
        db.delete(ev)

    if any(int(ev.event_n) != idx for idx, ev in enumerate(local_events, start=1)):
        for idx, ev in enumerate(local_events, start=1):
            ev.event_n = 1000 + idx
        db.flush()
        for idx, ev in enumerate(local_events, start=1):
            ev.event_n = idx
        db.flush()

    state = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if state is None:
        if not local_events:
            return 0
        state = SessionUserState(session_id=session_id, user_id=user_id, poops_n=0)
        db.add(state)
        db.flush()

    state.poops_n = len(local_events)
    if local_events:
        last_event = local_events[-1]
        state.bristol = last_event.bristol
        state.feeling = last_event.feeling
    else:
        state.achievement_text = None
        state.bristol = None
        state.feeling = None

    return len(local_events)


def ensure_events_count(db: Session, session_id: int, user_id: int, poops_n: int, origin_chat_id: int | None = None) -> None:
    if poops_n <= 0:
        return

    existing = {
        int(e.event_n): e
        for e in db.scalars(
            select(PoopEvent).where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id)
        ).all()
    }
    for n in range(1, int(poops_n) + 1):
        if n not in existing:
            create_event(db, session_id=session_id, user_id=user_id, event_n=n, origin_chat_id=origin_chat_id)


def reconcile_events_count(db: Session, session_id: int, user_id: int, poops_n: int, origin_chat_id: int | None = None) -> None:
    target = max(0, int(poops_n or 0))
    events = db.scalars(
        select(PoopEvent).where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id)
    ).all()
    existing = {int(e.event_n) for e in events}

    # Drop orphan tail events that exceed current poops_n.
    for n in sorted([n for n in existing if n > target], reverse=True):
        db.execute(
            delete(PoopEvent).where(
                PoopEvent.session_id == session_id,
                PoopEvent.user_id == user_id,
                PoopEvent.event_n == n,
            )
        )

    # Create missing events inside [1..poops_n].
    for n in range(1, target + 1):
        if n not in existing:
            create_event(db, session_id=session_id, user_id=user_id, event_n=n, origin_chat_id=origin_chat_id)


def create_event(db: Session, session_id: int, user_id: int, event_n: int, origin_chat_id: int | None = None) -> None:
    if origin_chat_id is None:
        origin_chat_id = int(
            db.scalar(
                select(PoopEvent.__table__.c.origin_chat_id)
                .where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id)
                .order_by(PoopEvent.event_n.asc())
                .limit(1)
            )
            or 0
        )
    if origin_chat_id == 0:
        from app.db.models import Session as DaySession

        origin_chat_id = int(db.scalar(select(DaySession.chat_id).where(DaySession.session_id == session_id)) or 0)

    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    if dialect_name == "postgresql":
        db.execute(
            pg_insert(PoopEvent)
            .values(session_id=session_id, user_id=user_id, event_n=event_n, origin_chat_id=origin_chat_id)
            .on_conflict_do_nothing(
                index_elements=["session_id", "user_id", "event_n"]
            )
        )
        return

    exists = db.scalar(
        select(PoopEvent.id).where(
            PoopEvent.session_id == session_id,
            PoopEvent.user_id == user_id,
            PoopEvent.event_n == event_n,
        ).limit(1)
    )
    if exists is not None:
        return
    db.execute(
        insert(PoopEvent).values(
            session_id=session_id,
            user_id=user_id,
            event_n=event_n,
            origin_chat_id=origin_chat_id,
        )
    )


def delete_event(db: Session, session_id: int, user_id: int, event_n: int) -> None:
    db.execute(
        delete(PoopEvent).where(
            PoopEvent.session_id == session_id,
            PoopEvent.user_id == user_id,
            PoopEvent.event_n == event_n,
        )
    )
