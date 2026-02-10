from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Chat,
    Participant,
    Q1Answer,
    Q2Answer,
    Q3Answer,
    Reminder,
    DailySession,
)


DEFAULT_DAILY_POST_HOUR = 10


async def ensure_chat_saved(chat_id: int, chat_type: str, title: str | None) -> None:
    """
    Upsert чата. Если чат уже есть — обновляем тип/название и включаем.
    daily_post_hour НЕ трогаем, если он уже выставлен (чтобы настройки чата не сбрасывались).
    """
    from app.db.engine import SessionMaker

    async with SessionMaker() as session:
        existing = await session.get(Chat, chat_id)
        if existing:
            existing.chat_type = chat_type
            existing.title = title
            existing.is_enabled = True
            if getattr(existing, "daily_post_hour", None) is None:
                existing.daily_post_hour = DEFAULT_DAILY_POST_HOUR
        else:
            session.add(
                Chat(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    title=title,
                    is_enabled=True,
                    daily_post_hour=DEFAULT_DAILY_POST_HOUR,
                )
            )
        await session.commit()


async def set_chat_post_hour(session: AsyncSession, chat_id: int, hour: int) -> None:
    """
    Сохранить настройку времени вопроса для конкретного чата.
    hour ожидается 10/14/20.
    """
    chat = await session.get(Chat, chat_id)
    if not chat:
        # если вдруг чата нет (редко), создадим минимум
        chat = Chat(chat_id=chat_id, chat_type="group", title=None,
                    is_enabled=True, daily_post_hour=hour)
        session.add(chat)
        await session.flush()
        return

    chat.daily_post_hour = hour
    await session.flush()


async def upsert_participant(chat_id: int, user_id: int, username: str | None, full_name: str) -> None:
    """
    Upsert участника. При любом появлении/ответе — снимаем opt-out.
    """
    from app.db.engine import SessionMaker

    async with SessionMaker() as session:
        q = select(Participant).where(Participant.chat_id ==
                                      chat_id, Participant.user_id == user_id)
        res = await session.execute(q)
        p = res.scalar_one_or_none()

        if p:
            p.username = username
            p.full_name = full_name
            p.is_opted_out = False
        else:
            session.add(
                Participant(
                    chat_id=chat_id,
                    user_id=user_id,
                    username=username,
                    full_name=full_name,
                    is_opted_out=False,
                )
            )
        await session.commit()


async def set_opt_out(session: AsyncSession, chat_id: int, user_id: int, value: bool) -> None:
    p = await session.scalar(select(Participant).where(Participant.chat_id == chat_id, Participant.user_id == user_id))
    if not p:
        return
    p.is_opted_out = value
    await session.flush()


async def wipe_user_data(session: AsyncSession, chat_id: int, user_id: int) -> None:
    """
    Полная очистка пользователя в рамках ОДНОГО чата:
    - удаляем q1/q2/q3 ответы
    - удаляем reminders
    - удаляем participant
    """
    # ответы Q1/Q2/Q3 (фильтруем по chat_id+user_id — этого достаточно)
    await session.execute(
        delete(Q1Answer).where(Q1Answer.chat_id ==
                               chat_id, Q1Answer.user_id == user_id)
    )
    await session.execute(
        delete(Q2Answer).where(Q2Answer.chat_id ==
                               chat_id, Q2Answer.user_id == user_id)
    )
    await session.execute(
        delete(Q3Answer).where(Q3Answer.chat_id ==
                               chat_id, Q3Answer.user_id == user_id)
    )

    # reminders
    await session.execute(
        delete(Reminder).where(Reminder.chat_id ==
                               chat_id, Reminder.user_id == user_id)
    )

    # сам участник
    await session.execute(
        delete(Participant).where(Participant.chat_id ==
                                  chat_id, Participant.user_id == user_id)
    )

    await session.flush()
