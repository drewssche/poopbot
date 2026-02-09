from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Participant, Q1Answer, Q2Answer, Reminder, DailySession


async def set_opt_out(session: AsyncSession, chat_id: int, user_id: int, value: bool) -> None:
    q = select(Participant).where(Participant.chat_id ==
                                  chat_id, Participant.user_id == user_id)
    res = await session.execute(q)
    p = res.scalar_one_or_none()
    if not p:
        return
    p.is_opted_out = value
    await session.flush()


async def wipe_user_data(session: AsyncSession, chat_id: int, user_id: int) -> None:
    # Сначала найдём все session_id данного чата (через daily_sessions)
    q = select(DailySession.id).where(DailySession.chat_id == chat_id)
    res = await session.execute(q)
    sess_ids = [x[0] for x in res.all()]

    if sess_ids:
        # q1/q2 ответы пользователя в этом чате
        await session.execute(
            delete(Q1Answer).where(Q1Answer.chat_id ==
                                   chat_id, Q1Answer.user_id == user_id)
        )
        await session.execute(
            delete(Q2Answer).where(Q2Answer.chat_id ==
                                   chat_id, Q2Answer.user_id == user_id)
        )
        # reminders привязаны к session_id, но тоже фильтруем по chat_id/user_id
        await session.execute(
            delete(Reminder).where(Reminder.chat_id ==
                                   chat_id, Reminder.user_id == user_id)
        )

    # и самого участника
    await session.execute(
        delete(Participant).where(Participant.chat_id ==
                                  chat_id, Participant.user_id == user_id)
    )

    await session.flush()
