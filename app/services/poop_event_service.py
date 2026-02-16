from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import PoopEvent


def list_events(db: Session, session_id: int, user_id: int) -> list[PoopEvent]:
    return db.scalars(
        select(PoopEvent)
        .where(PoopEvent.session_id == session_id, PoopEvent.user_id == user_id)
        .order_by(PoopEvent.event_n.asc())
    ).all()


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

    db.execute(
        pg_insert(PoopEvent)
        .values(session_id=session_id, user_id=user_id, event_n=event_n, origin_chat_id=origin_chat_id)
        .on_conflict_do_nothing(
            index_elements=["session_id", "user_id", "event_n"]
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
