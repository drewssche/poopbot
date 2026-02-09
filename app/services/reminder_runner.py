from sqlalchemy import select
from datetime import datetime, timezone

from app.db.engine import SessionMaker
from app.db.models import Reminder, DailySession, Participant
from app.services.mentions import mention_user
from app.services.timeutils import now_local, is_after_close


async def run_due_reminders(bot) -> None:
    # После 23:55 локального времени напоминания не шлём
    if is_after_close(now_local()):
        return

    now = datetime.now(timezone.utc)

    async with SessionMaker() as session:
        q = select(Reminder).where(Reminder.is_sent == False, Reminder.remind_at <= now)  # noqa: E712
        res = await session.execute(q)
        reminders = list(res.scalars().all())

        if not reminders:
            return

        for r in reminders:
            sess = await session.get(DailySession, r.session_id)
            if not sess or not sess.message1_id:
                r.is_sent = True
                continue

            pq = select(Participant).where(
                Participant.chat_id == r.chat_id,
                Participant.user_id == r.user_id
            )
            pres = await session.execute(pq)
            p = pres.scalar_one_or_none()

            m = mention_user(p.user_id, p.full_name,
                             p.username) if p else "друг"

            try:
                await bot.send_message(
                    chat_id=r.chat_id,
                    text=f"Ну что, {m}, ты покакал?",
                    reply_to_message_id=int(sess.message1_id),
                    parse_mode="HTML",
                )
            except Exception:
                # не спамим бесконечно
                pass

            r.is_sent = True

        await session.commit()
