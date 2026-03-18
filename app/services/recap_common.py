from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ChatMember, PoopEvent
from app.db.models import Session as DaySession


def year_flavor(year: int) -> tuple[str, str, str]:
    packs = {
        2026: (
            "Год боевого ритма",
            "Этот год проверял дисциплину. Ты держался(ась) достойно.",
            "Финал года принят. Новый сезон можно открывать.",
        ),
    }
    return packs.get(
        year,
        (
            "Год в потоке",
            "Год был длинный, но ритм ты не потерял(а).",
            "Итоги зафиксированы. Дальше — только стабильнее.",
        ),
    )


def phrase_toilet(day_key: str, count: int) -> str:
    variants = {
        "feb9": (
            "Праздник был, активности не было.",
            "Праздник отмечен достойно.",
            "Праздник прошёл по-королевски.",
        ),
        "nov19": (
            "День прошёл в режиме наблюдателя.",
            "Профессиональный минимум выполнен.",
            "Всемирный день отработан с мировым размахом.",
        ),
    }
    zero, low, high = variants[day_key]
    if count <= 0:
        return zero
    if count <= 2:
        return low
    return high


def bot_first_interaction_date(db: Session) -> date | None:
    return db.scalar(select(func.min(DaySession.session_date)))


def recap_target_year(today: date) -> int:
    if today.month == 1 and today.day <= 3:
        return today.year - 1
    return today.year


def is_recap_available(today: date, user_id: int, owner_id: int | None) -> bool:
    if owner_id is not None and int(user_id) == int(owner_id):
        return True
    return (today.month == 12 and today.day >= 30) or (today.month == 1 and today.day <= 3)


def list_user_recap_chat_ids(db: Session, user_id: int, year: int) -> list[int]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    rows = db.scalars(
        select(DaySession.chat_id)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id < 0,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
            PoopEvent.user_id == user_id,
            PoopEvent.origin_chat_id == DaySession.chat_id,
        )
        .group_by(DaySession.chat_id)
        .order_by(DaySession.chat_id.asc())
    ).all()
    return [int(cid) for cid in rows]


def list_user_member_chat_ids(db: Session, user_id: int) -> list[int]:
    rows = db.scalars(
        select(ChatMember.chat_id)
        .where(
            ChatMember.user_id == user_id,
            ChatMember.chat_id < 0,
        )
        .group_by(ChatMember.chat_id)
        .order_by(ChatMember.chat_id.asc())
    ).all()
    return [int(cid) for cid in rows]


def pick_user_recap_source_chat(db: Session, user_id: int, year: int) -> int | None:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    row = db.execute(
        select(
            DaySession.chat_id,
            func.count(PoopEvent.id).label("poops"),
        )
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id < 0,
            DaySession.session_date >= start,
            DaySession.session_date <= end,
            PoopEvent.user_id == user_id,
            PoopEvent.origin_chat_id == DaySession.chat_id,
        )
        .group_by(DaySession.chat_id)
        .order_by(func.count(PoopEvent.id).desc(), DaySession.chat_id.asc())
    ).first()
    if row is None:
        return None
    return int(row.chat_id)
