import logging
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.engine import SessionMaker
from app.db.models import DailySession
from app.services.timeutils import today_local_date, now_local, is_after_close

log = logging.getLogger(__name__)


async def close_today_sessions(bot) -> None:
    now = now_local()

    # если ещё не 23:55 — ничего не делаем
    if not is_after_close(now):
        return

    today = today_local_date()

    async with SessionMaker() as session:
        q = select(DailySession).where(DailySession.day == today, DailySession.is_closed == False)  # noqa: E712
        res = await session.execute(q)
        sessions = list(res.scalars().all())

        if not sessions:
            return

        for s in sessions:
            # Убираем клавиатуру у Q1
            if s.message1_id:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=s.chat_id,
                        message_id=int(s.message1_id),
                        reply_markup=None
                    )
                except Exception:
                    log.exception("Failed to remove markup from Q1 message")

            # Убираем клавиатуру у Q2 (если было)
            if s.message2_id:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=s.chat_id,
                        message_id=int(s.message2_id),
                        reply_markup=None
                    )
                except Exception:
                    log.exception("Failed to remove markup from Q2 message")

            s.is_closed = True
            s.closed_at = datetime.now(timezone.utc)

        await session.commit()
