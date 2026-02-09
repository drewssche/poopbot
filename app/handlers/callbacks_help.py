import logging

from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from app.handlers.stats_kb import kb_stats
from app.services.help_menu import kb_help, kb_settings
from app.services.help_texts import help_text, settings_text, about_text
from app.services.stats import build_stats_text
from app.services.user_data import set_opt_out, wipe_user_data
from app.services.mentions import mention_user
from app.db.engine import SessionMaker
from app.services.timeutils import today_local_date
from app.services.q1_storage import get_or_create_session
from app.services.text_builders import build_q1_text
from app.services.question1 import kb_question1


def register(dp) -> None:
    @dp.callback_query(F.data.startswith("help:"))
    async def on_help_menu(call: CallbackQuery):
        if not call.message:
            await call.answer("Ошибка")
            return

        action = call.data.split(":", 1)[1]
        user = call.from_user

        if action == "settings":
            m = mention_user(user.id, user.full_name, user.username)
            await call.message.edit_text(settings_text(m), reply_markup=kb_settings(user.id))
            await call.answer()

        elif action == "stats":
            chat_id = call.message.chat.id
            text = await build_stats_text(chat_id, "today")
            await call.message.edit_text(text, reply_markup=kb_stats("today"))
            await call.answer()

        elif action == "about":
            await call.message.edit_text(about_text(), reply_markup=kb_help())
            await call.answer()

        else:
            await call.answer("Неизвестно")

    @dp.callback_query(F.data.startswith("set:"))
    async def on_settings(call: CallbackQuery):
        if not call.message:
            await call.answer("Ошибка")
            return

        parts = call.data.split(":")
        if len(parts) != 3:
            await call.answer("Ошибка данных")
            return

        action = parts[1]
        owner_id = int(parts[2])

        if call.from_user.id != owner_id:
            await call.answer("Это меню не для тебя", show_alert=False)
            return

        chat_id = call.message.chat.id
        user = call.from_user
        day = today_local_date()

        async with SessionMaker() as session:
            if action == "optout":
                await set_opt_out(session, chat_id, user.id, True)
                await session.commit()
                await call.message.edit_text("🚫 Ок. Я больше не буду тебя тегать.", reply_markup=kb_help())
                await call.answer()

            elif action == "wipe":
                await wipe_user_data(session, chat_id, user.id)
                await session.commit()
                await call.message.edit_text("🧹 Готово. Я удалил твои данные из этого чата.", reply_markup=kb_help())
                await call.answer()

            elif action == "back":
                await call.message.edit_text(help_text(), reply_markup=kb_help())
                await call.answer()

            else:
                await call.answer("Неизвестное действие")

        # Обновляем первый вопрос, чтобы пользователь сразу исчез из списка
        async with SessionMaker() as session:
            sess = await get_or_create_session(session, chat_id, day)
            msg_id = sess.message1_id
            is_closed = getattr(sess, "is_closed", False)

        if msg_id and not is_closed:
            text = await build_q1_text(chat_id)
            try:
                await call.message.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(msg_id),
                    text=text,
                    reply_markup=kb_question1(),
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    logging.exception("Failed to edit Q1 message text after settings")
            except Exception:
                logging.exception("Failed to edit Q1 message text after settings")

    @dp.callback_query(F.data.startswith("stats:"))
    async def on_stats(call: CallbackQuery):
        if not call.message:
            await call.answer("Ошибка")
            return

        kind = call.data.split(":", 1)[1]

        if kind == "back":
            await call.message.edit_text(help_text(), reply_markup=kb_help())
            await call.answer()
            return

        chat_id = call.message.chat.id
        text = await build_stats_text(chat_id, kind)
        await call.message.edit_text(text, reply_markup=kb_stats(kind))
        await call.answer()
