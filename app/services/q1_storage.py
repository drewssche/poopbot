from datetime import date, datetime, timedelta

from sqlalchemy import select, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailySession, Q1Answer, Participant


async def get_or_create_session(session: AsyncSession, chat_id: int, day: date) -> DailySession:
    q = select(DailySession).where(
        DailySession.chat_id == chat_id, DailySession.day == day)
    res = await session.execute(q)
    s = res.scalar_one_or_none()
    if s:
        return s
    s = DailySession(chat_id=chat_id, day=day)
    session.add(s)
    await session.flush()  # получить s.id
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


async def get_q1_answers_map(session: AsyncSession, sess_id: int) -> dict[int, tuple[str, datetime | None]]:
    q = select(Q1Answer).where(Q1Answer.session_id == sess_id)
    res = await session.execute(q)
    mp: dict[int, tuple[str, datetime | None]] = {}
    for row in res.scalars().all():
        mp[row.user_id] = (row.answer, row.remind_at)
    return mp


async def user_already_answered_q1(session: AsyncSession, sess_id: int, user_id: int) -> bool:
    q = select(Q1Answer.id).where(Q1Answer.session_id ==
                                  sess_id, Q1Answer.user_id == user_id)
    res = await session.execute(q)
    return res.scalar_one_or_none() is not None


async def insert_q1_answer(
    session: AsyncSession,
    sess: DailySession,
    chat_id: int,
    user_id: int,
    answer: str,
    remind_at: datetime | None,
) -> None:
    session.add(Q1Answer(session_id=sess.id, chat_id=chat_id,
                user_id=user_id, answer=answer, remind_at=remind_at))
    await session.flush()


async def calc_streak_for_user(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    today: date,
) -> tuple[int, date | None]:
    """
    Стрик по 💩: считаем подряд идущие дни, где ответ = 'poop'.
    Возвращаем (кол-во дней, дата старта стрика) либо (0, None).
    """
    # Берём последние ответы по дням, начиная с today, только для чата и юзера
    # Чтобы не таскать много, ограничим верхом 365 (друзья/малые чаты — ок)
    q = (
        select(DailySession.day, Q1Answer.answer)
        .join(Q1Answer, Q1Answer.session_id == DailySession.id)
        .where(DailySession.chat_id == chat_id, Q1Answer.user_id == user_id)
        .order_by(desc(DailySession.day))
        .limit(365)
    )
    res = await session.execute(q)
    rows = res.all()

    streak = 0
    start_day: date | None = None

    expected = today
    for day, ans in rows:
        if day != expected:
            # пропуск дня — стрик рвётся
            break
        if ans != "poop":
            break
        streak += 1
        start_day = day
        expected = expected - timedelta(days=1)

    # start_day сейчас указывает на самый ранний день, дошли ли мы до него
    if streak == 0:
        return 0, None
    # дата старта — это today - (streak-1)
    real_start = today - timedelta(days=streak - 1)
    return streak, real_start


async def get_q1_answer(session: AsyncSession, sess_id: int, user_id: int) -> Q1Answer | None:
    q = select(Q1Answer).where(Q1Answer.session_id ==
                               sess_id, Q1Answer.user_id == user_id)
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def update_q1_answer(
    session: AsyncSession,
    sess_id: int,
    user_id: int,
    answer: str,
    remind_at: datetime | None,
) -> None:
    await session.execute(
        update(Q1Answer)
        .where(Q1Answer.session_id == sess_id, Q1Answer.user_id == user_id)
        .values(answer=answer, remind_at=remind_at)
    )
    await session.flush()


async def get_q1_poop_user_ids(session: AsyncSession, sess_id: int) -> list[int]:
    q = select(Q1Answer.user_id).where(
        Q1Answer.session_id == sess_id, Q1Answer.answer == "poop")
    res = await session.execute(q)
    return [x[0] for x in res.all()]


async def is_session_closed(session: AsyncSession, chat_id: int, day) -> bool:
    q = select(DailySession.is_closed).where(
        DailySession.chat_id == chat_id, DailySession.day == day)
    res = await session.execute(q)
    val = res.scalar_one_or_none()
    return bool(val) if val is not None else False
