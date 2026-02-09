import logging

from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

from app.services.help_menu import kb_help
from app.services.help_texts import help_text
from app.services.stats import build_stats_text
from app.handlers.stats_kb import kb_stats
from app.services.text_builders import build_q1_text
from app.services.timeutils import today_local_date
from app.db.engine import SessionMaker
from app.services.q1_storage import get_or_create_session, set_message1_id
from app.services.question1 import kb_question1
from app.services.user_data import ensure_chat_saved


def register(dp, bot) -> None:
    @dp.message(Command("start"))
    async def cmd_start(message):
        chat = message.chat
        title = getattr(chat, "title", None)

        await ensure_chat_saved(chat_id=chat.id, chat_type=chat.type, title=title)

        day = today_local_date()

        async with SessionMaker() as session:
            sess = await get_or_create_session(session, chat.id, day)
            existing_msg_id = sess.message1_id
            is_closed = getattr(sess, "is_closed", False)

        text = await build_q1_text(chat.id)

        # 1) Первый пост за сессию — БЕЗ реплая
        if not existing_msg_id:
            sent = await message.answer(text, reply_markup=None if is_closed else kb_question1())
            async with SessionMaker() as session:
                sess2 = await get_or_create_session(session, chat.id, day)
                await set_message1_id(session, sess2, sent.message_id)
                await session.commit()
            logging.info(
                "Start: first Q1 created without reply: chat_id=%s msg_id=%s",
                chat.id,
                sent.message_id,
            )
            return

        # 2) Если пост уже есть — пробуем обновить
        edit_ok = True
        try:
            await bot.edit_message_text(
                chat_id=chat.id,
                message_id=int(existing_msg_id),
                text=text,
                reply_markup=None if is_closed else kb_question1(),
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                edit_ok = True
            else:
                edit_ok = False
                logging.exception("Start: failed to edit existing Q1 (bad request)")
        except Exception:
            edit_ok = False
            logging.exception("Start: failed to edit existing Q1 (unknown)")

        # 3) Если обновили/не надо было обновлять — реплай на актуальный пост
        if edit_ok:
            await bot.send_message(
                chat_id=chat.id,
                text="Актуальный опрос на сегодня — вот он 👇",
                reply_to_message_id=int(existing_msg_id),
            )
            return

        # 4) Если не смогли править — создаём новый и реплаим на него
        sent = await message.answer(text, reply_markup=None if is_closed else kb_question1())
        async with SessionMaker() as session:
            sess3 = await get_or_create_session(session, chat.id, day)
            await set_message1_id(session, sess3, sent.message_id)
            await session.commit()

        await bot.send_message(
            chat_id=chat.id,
            text="Актуальный опрос на сегодня — вот он 👇",
            reply_to_message_id=int(sent.message_id),
        )

    @dp.message(Command("help"))
    async def cmd_help(message):
        await message.answer(help_text(), reply_markup=kb_help())

    @dp.message(Command("stats"))
    async def cmd_stats(message):
        chat_id = message.chat.id
        text = await build_stats_text(chat_id, "today")
        await message.answer(text, reply_markup=kb_stats("today"))
