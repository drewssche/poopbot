import logging
from sqlalchemy import select

from app.db.engine import SessionMaker
from app.db.models import Chat
from app.services.q1_storage import get_or_create_session, set_message1_id
from app.services.timeutils import today_local_date

log = logging.getLogger(__name__)


async def post_daily_q1(bot, build_q1_text, kb_question1) -> None:
    day = today_local_date()

    async with SessionMaker() as session:
        q = select(Chat).where(Chat.is_enabled == True)  # noqa: E712
        res = await session.execute(q)
        chats = list(res.scalars().all())

    for c in chats:
        try:
            async with SessionMaker() as session:
                sess = await get_or_create_session(session, c.chat_id, day)
                # если уже есть message1_id — значит на сегодня уже постили
                if sess.message1_id:
                    continue

            text = await build_q1_text(c.chat_id)
            sent = await bot.send_message(chat_id=c.chat_id, text=text, reply_markup=kb_question1())

            async with SessionMaker() as session:
                sess = await get_or_create_session(session, c.chat_id, day)
                await set_message1_id(session, sess, sent.message_id)
                await session.commit()

        except Exception:
            log.exception("Failed to post daily Q1 for chat_id=%s", c.chat_id)
