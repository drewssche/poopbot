from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailySession, Q1Answer, Participant


# -------------------------
# Sessions / participants
# -------------------------

async def get_or_create_session(session: AsyncSession, chat_id: int, day: date) -> DailySession:
    q = select(DailySession).where(
        DailySession.chat_id == chat_id, DailySession.day == day)
    res = await session.execute(q)
    s = res.scalar_one_or_none()
    if s:
        return s

    s = DailySession(chat_id=chat_id, day=day)
    session.add(s)
    await session.flush()
    return s


async def set_message1_id(session: AsyncSession, sess: DailySession, message1_id: int) -> None:
    sess.message1_id = message1_id
    await session.flush()


async def get_active_participants(session: AsyncSession, chat_id: int) -> list[Participant]:
    q = (
        select(Participant)
        .where(Participant.chat_id == chat_id, Participant.is_opted_out == False)  # noqa: E712
        .order_by(Participant.id.asc())
    )
    res = await session.execute(q)
    return list(res.scalars().all())


# -------------------------
# Q1 storage (count + remind)
# -------------------------

async def get_q1_row(session: AsyncSession, sess_id: int, user_id: int) -> Q1Answer | None:
    q = select(Q1Answer).where(Q1Answer.session_id ==
                               sess_id, Q1Answer.user_id == user_id)
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def get_or_create_q1_row(
    session: AsyncSession,
    sess: DailySession,
    chat_id: int,
    user_id: int,
) -> Q1Answer:
    row = await get_q1_row(session, sess.id, user_id)
    if row:
        return row

    row = Q1Answer(session_id=sess.id, chat_id=chat_id,
                   user_id=user_id, poop_count=0, remind_at=None)
    session.add(row)
    await session.flush()
    return row


async def get_q1_state_map(session: AsyncSession, sess_id: int) -> dict[int, tuple[int, datetime | None]]:
    """
    Возвращает mp[user_id] = (poop_count, remind_at)
    """
    q = select(Q1Answer).where(Q1Answer.session_id == sess_id)
    res = await session.execute(q)

    mp: dict[int, tuple[int, datetime | None]] = {}
    for row in res.scalars().all():
        mp[row.user_id] = (int(row.poop_count), row.remind_at)
    return mp


async def update_q1_count_and_touch(
    session: AsyncSession,
    sess_id: int,
    user_id: int,
    poop_count: int,
    now_utc: datetime,
) -> None:
    await session.execute(
        update(Q1Answer)
        .where(Q1Answer.session_id == sess_id, Q1Answer.user_id == user_id)
        .values(poop_count=poop_count, last_action_at=now_utc)
    )
    await session.flush()


async def increment_q1_count(
    session: AsyncSession,
    sess: DailySession,
    chat_id: int,
    user_id: int,
    now_utc: datetime,
    max_count: int = 10,
) -> int:
    row = await get_or_create_q1_row(session, sess, chat_id, user_id)
    new_count = int(row.poop_count) + 1
    if new_count > max_count:
        # не меняем
        return int(row.poop_count)

    await update_q1_count_and_touch(session, sess.id, user_id, new_count, now_utc)
    return new_count


async def decrement_q1_count(
    session: AsyncSession,
    sess: DailySession,
    chat_id: int,
    user_id: int,
    now_utc: datetime,
) -> int:
    row = await get_or_create_q1_row(session, sess, chat_id, user_id)
    new_count = int(row.poop_count) - 1
    if new_count < 0:
        # не меняем
        return int(row.poop_count)

    await update_q1_count_and_touch(session, sess.id, user_id, new_count, now_utc)
    return new_count


async def set_q1_remind_at(
    session: AsyncSession,
    sess: DailySession,
    chat_id: int,
    user_id: int,
    remind_at_utc: datetime,
    now_utc: datetime,
) -> None:
    """
    Ставит remind_at (UTC) для пользователя на текущую сессию.
    """
    await get_or_create_q1_row(session, sess, chat_id, user_id)
    await session.execute(
        update(Q1Answer)
        .where(Q1Answer.session_id == sess.id, Q1Answer.user_id == user_id)
        .values(remind_at=remind_at_utc, last_action_at=now_utc)
    )
    await session.flush()


async def cancel_q1_remind(
    session: AsyncSession,
    sess_id: int,
    user_id: int,
    now_utc: datetime,
) -> None:
    await session.execute(
        update(Q1Answer)
        .where(Q1Answer.session_id == sess_id, Q1Answer.user_id == user_id)
        .values(remind_at=None, last_action_at=now_utc)
    )
    await session.flush()


async def get_q1_positive_user_ids(session: AsyncSession, sess_id: int) -> list[int]:
    q = select(Q1Answer.user_id).where(
        Q1Answer.session_id == sess_id, Q1Answer.poop_count > 0)
    res = await session.execute(q)
    return [x[0] for x in res.all()]


# -------------------------
# Streak (history-based fallback)
# -------------------------

async def calc_streak_for_user(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    today: date,
) -> tuple[int, date | None]:
    """
    Фоллбек пересчёт стрика из истории:
    подряд идущие дни, где poop_count > 0.

    В финале мы будем поддерживать current_streak_days в Participant при закрытии дня,
    но этот метод полезен для совместимости/проверок.
    """
    q = (
        select(DailySession.day, Q1Answer.poop_count)
        .join(Q1Answer, Q1Answer.session_id == DailySession.id)
        .where(DailySession.chat_id == chat_id, Q1Answer.user_id == user_id)
        .order_by(desc(DailySession.day))
        .limit(365)
    )
    res = await session.execute(q)
    rows = res.all()

    streak = 0
    expected = today

    for day_val, cnt in rows:
        if day_val != expected:
            break
        if int(cnt) <= 0:
            break
        streak += 1
        expected = expected - timedelta(days=1)

    if streak == 0:
        return 0, None
    start_day = today - timedelta(days=streak - 1)
    return streak, start_day


async def is_session_closed(session: AsyncSession, chat_id: int, day: date) -> bool:
    q = select(DailySession.is_closed).where(
        DailySession.chat_id == chat_id, DailySession.day == day)
    res = await session.execute(q)
    val = res.scalar_one_or_none()
    return bool(val) if val is not None else False


# -------------------------
# Backward compatible API
# -------------------------

async def get_q1_answers_map(session: AsyncSession, sess_id: int):
    """
    Backward-compatible alias.

    Старый код ожидал get_q1_answers_map(), теперь это get_q1_state_map().

    Возвращаем тот же формат:
    mp[user_id] = (poop_count, remind_at)
    """
    return await get_q1_state_map(session, sess_id)
