import logging

from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from app.db.engine import SessionMaker
from app.services.timeutils import today_local_date, now_local, calc_remind_at, can_schedule_later
from app.services.q1_storage import (
    get_or_create_session,
    get_q1_answer,
    insert_q1_answer,
    update_q1_answer,
)
from app.services.q2_storage import get_q2_answer, set_q2_answer
from app.services.reminders import upsert_reminder, cancel_reminder, get_existing_reminder
from app.services.question1 import kb_question1
from app.services.question2 import kb_question2
from app.services.text_builders import build_q1_text, build_q2_text
from app.services.user_data import upsert_participant


def register(dp, bot) -> None:
    @dp.callback_query(F.data.startswith("q1:"))
    async def on_q1_click(call: CallbackQuery):
        if not call.message:
            await call.answer("Ошибка: нет сообщения")
            return

        chat_id = call.message.chat.id
        user = call.from_user
        day = today_local_date()

        await upsert_participant(chat_id, user.id, user.username, user.full_name)

        action = call.data.split(":", 1)[1]  # poop / no / later
        now = now_local()

        async with SessionMaker() as session:
            sess = await get_or_create_session(session, chat_id, day)

            if getattr(sess, "is_closed", False):
                await call.answer("Сессия закрыта", show_alert=False)
                return

            existing = await get_q1_answer(session, sess.id, user.id)

            if action == "later":
                if not can_schedule_later(now):
                    await call.answer("Поздно: напоминания доступны до 21:55", show_alert=False)
                    return

                if existing and existing.answer in ("poop", "no"):
                    await call.answer("На сегодня ты ответил", show_alert=False)
                    return

                existing_rem = await get_existing_reminder(session, chat_id, sess.id, user.id)
                if existing_rem and not existing_rem.is_sent:
                    await call.answer("Напоминание уже запланировано", show_alert=False)
                    return

                remind_at = calc_remind_at(now)

                if existing is None:
                    await insert_q1_answer(session, sess, chat_id, user.id, "later", remind_at)
                else:
                    await update_q1_answer(session, sess.id, user.id, "later", remind_at)

                await upsert_reminder(session, chat_id, sess.id, user.id, remind_at)
                await session.commit()

            elif action in ("poop", "no"):
                if existing and existing.answer in ("poop", "no"):
                    await call.answer("На сегодня ты ответил", show_alert=False)
                    return

                if existing is None:
                    await insert_q1_answer(session, sess, chat_id, user.id, action, None)
                else:
                    await update_q1_answer(session, sess.id, user.id, action, None)

                await cancel_reminder(session, chat_id, sess.id, user.id)
                await session.commit()

            else:
                await call.answer("Неизвестная кнопка", show_alert=False)
                return

        new_text = await build_q1_text(chat_id)
        try:
            await call.message.edit_text(new_text, reply_markup=kb_question1())
        except Exception:
            logging.exception("Failed to edit Q1 message text")

        if action == "poop":
            async with SessionMaker() as session:
                sess2 = await get_or_create_session(session, chat_id, day)

                text2 = await build_q2_text(chat_id, sess2.id)
                if text2:
                    if not sess2.message2_id:
                        sent2 = await bot.send_message(chat_id=chat_id, text=text2, reply_markup=kb_question2())
                        sess2.message2_id = sent2.message_id
                        await session.commit()
                    else:
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=int(sess2.message2_id),
                                text=text2,
                                reply_markup=kb_question2(),
                            )
                        except TelegramBadRequest as e:
                            if "message is not modified" in str(e):
                                pass
                            else:
                                raise

        await call.answer("Принято ✅", show_alert=False)

    @dp.callback_query(F.data.startswith("q2:"))
    async def on_q2_click(call: CallbackQuery):
        if not call.message:
            await call.answer("Ошибка: нет сообщения")
            return

        chat_id = call.message.chat.id
        user = call.from_user
        day = today_local_date()

        action = call.data.split(":", 1)[1]  # good / ok / bad

        async with SessionMaker() as session:
            sess = await get_or_create_session(session, chat_id, day)

            if getattr(sess, "is_closed", False):
                await call.answer("Сессия закрыта", show_alert=False)
                return

            q1 = await get_q1_answer(session, sess.id, user.id)

            if not q1 or q1.answer == "later":
                await call.answer("Сначала ответь, какал ли", show_alert=False)
                return

            if q1.answer == "no":
                await call.answer("Ты сегодня не какал", show_alert=False)
                return

            if q1.answer != "poop":
                await call.answer("Сначала ответь, какал ли", show_alert=False)
                return

            existing = await get_q2_answer(session, sess.id, user.id)
            if existing:
                await call.answer("На сегодня ты ответил", show_alert=False)
                return

            await set_q2_answer(session, sess.id, chat_id, user.id, action)
            await session.commit()

        async with SessionMaker() as session:
            sess2 = await get_or_create_session(session, chat_id, day)
            text2 = await build_q2_text(chat_id, sess2.id)

        try:
            await call.message.edit_text(text2, reply_markup=kb_question2())
        except Exception:
            logging.exception("Failed to edit Q2 message text")

        await call.answer("Принято ✅", show_alert=False)
