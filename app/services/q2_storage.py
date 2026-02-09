from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Q2Answer


async def get_q2_answer(session: AsyncSession, sess_id: int, user_id: int) -> Q2Answer | None:
    q = select(Q2Answer).where(Q2Answer.session_id ==
                               sess_id, Q2Answer.user_id == user_id)
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def set_q2_answer(session: AsyncSession, sess_id: int, chat_id: int, user_id: int, answer: str) -> None:
    existing = await get_q2_answer(session, sess_id, user_id)
    if existing:
        await session.execute(
            update(Q2Answer)
            .where(Q2Answer.session_id == sess_id, Q2Answer.user_id == user_id)
            .values(answer=answer)
        )
    else:
        session.add(Q2Answer(session_id=sess_id, chat_id=chat_id,
                    user_id=user_id, answer=answer))
    await session.flush()


async def get_q2_answers_map(session: AsyncSession, sess_id: int) -> dict[int, str]:
    q = select(Q2Answer).where(Q2Answer.session_id == sess_id)
    res = await session.execute(q)
    mp: dict[int, str] = {}
    for row in res.scalars().all():
        mp[row.user_id] = row.answer
    return mp
