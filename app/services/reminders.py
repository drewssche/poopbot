from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Reminder


async def get_existing_reminder(session: AsyncSession, chat_id: int, session_id: int, user_id: int) -> Reminder | None:
    q = select(Reminder).where(
        Reminder.chat_id == chat_id,
        Reminder.session_id == session_id,
        Reminder.user_id == user_id,
    )
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def upsert_reminder(session: AsyncSession, chat_id: int, session_id: int, user_id: int, remind_at: datetime) -> None:
    existing = await get_existing_reminder(session, chat_id, session_id, user_id)
    if existing:
        # если уже отправлено — не трогаем
        if existing.is_sent:
            return
        existing.remind_at = remind_at
    else:
        session.add(Reminder(chat_id=chat_id, session_id=session_id,
                    user_id=user_id, remind_at=remind_at, is_sent=False))
    await session.flush()


async def mark_sent(session: AsyncSession, reminder_id: int) -> None:
    await session.execute(update(Reminder).where(Reminder.id == reminder_id).values(is_sent=True))
    await session.flush()


async def cancel_reminder(session: AsyncSession, chat_id: int, session_id: int, user_id: int) -> None:
    existing = await get_existing_reminder(session, chat_id, session_id, user_id)
    if not existing:
        return
    # если уже отправили — не откатываем, просто оставим sent
    if existing.is_sent:
        return
    # удалять можно, но проще пометить как sent, чтобы больше не пытаться
    await session.execute(update(Reminder).where(Reminder.id == existing.id).values(is_sent=True))
    await session.flush()
