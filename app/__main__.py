import os
import asyncio
import logging
import calendar
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sqlalchemy import select

from app.db.init_db import init_db
from app.db.engine import SessionMaker
from app.db.models import Chat, Participant, DailySession, Q1Answer, Q2Answer

from app.services.question1 import kb_question1, render_question1_empty
from app.services.timeutils import (
    today_local_date,
    now_local,
    fmt_day,
    calc_remind_at,
    can_schedule_later,
)
from app.services.mentions import mention_user
from app.services.render_q1 import render_q1, status_text, streak_text
from app.services.q1_storage import (
    get_or_create_session,
    set_message1_id,
    get_active_participants,
    get_q1_answers_map,
    insert_q1_answer,
    calc_streak_for_user,
    get_q1_answer,
    update_q1_answer,
    get_q1_poop_user_ids,
)

from app.services.reminders import upsert_reminder, cancel_reminder, get_existing_reminder
from app.services.reminder_runner import run_due_reminders

from app.services.question2 import kb_question2
from app.services.render_q2 import render_q2, status_q2_text
from app.services.q2_storage import get_q2_answers_map, set_q2_answer, get_q2_answer

from app.services.session_closer import close_today_sessions
from app.services.daily_poster import post_daily_q1

from app.services.help_menu import kb_help, kb_settings
from app.services.help_texts import help_text, settings_text, about_text
from app.services.user_data import set_opt_out, wipe_user_data

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")


# ---------------------------
# Stats helpers
# ---------------------------

def kb_stats(selected: str = "today") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    def label(code: str, text: str) -> str:
        return f"• {text}" if code == selected else text

    kb.row(InlineKeyboardButton(text=label(
        "today", "Сегодня"), callback_data="stats:today"))
    kb.row(InlineKeyboardButton(text=label(
        "week", "На этой неделе"), callback_data="stats:week"))
    kb.row(InlineKeyboardButton(text=label(
        "month", "В этом месяце"), callback_data="stats:month"))
    kb.row(InlineKeyboardButton(text=label(
        "year", "В этом году"), callback_data="stats:year"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back"))
    return kb.as_markup()


def period_range(kind: str, today: date) -> tuple[date, date, str]:
    if kind == "today":
        return today, today, f"📊 Статистика за сегодня ({today.strftime('%d.%m.%y')})"
    if kind == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end, f"📊 Статистика за неделю ({start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')})"
    if kind == "month":
        start = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day)
        return start, end, f"📊 Статистика за месяц ({start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')})"
    if kind == "year":
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)
        return start, end, f"📊 Статистика за год ({start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')})"
    return today, today, f"📊 Статистика за сегодня ({today.strftime('%d.%m.%y')})"


async def build_stats_text(chat_id: int, kind: str) -> str:
    today = today_local_date()
    start, end, title = period_range(kind, today)

    async with SessionMaker() as session:
        participants = await get_active_participants(session, chat_id)

        if not participants:
            return (
                f"{title}\n\n"
                "Пока здесь никого нет в списке.\n"
                "Нажми любую кнопку в ежедневном опросе, чтобы добавиться."
            )

        # Q1: user_id, answer, day
        q1q = (
            select(Q1Answer.user_id, Q1Answer.answer, DailySession.day)
            .join(DailySession, DailySession.id == Q1Answer.session_id)
            .where(
                Q1Answer.chat_id == chat_id,
                DailySession.chat_id == chat_id,
                DailySession.day >= start,
                DailySession.day <= end,
            )
        )
        q1res = await session.execute(q1q)
        q1_rows = q1res.all()

        poop_counts: dict[int, int] = {}
        no_counts: dict[int, int] = {}
        answered_users: set[int] = set()

        for user_id, ans, _day in q1_rows:
            answered_users.add(user_id)
            if ans == "poop":
                poop_counts[user_id] = poop_counts.get(user_id, 0) + 1
            elif ans == "no":
                no_counts[user_id] = no_counts.get(user_id, 0) + 1

        # Q2: user_id, answer, day
        q2q = (
            select(Q2Answer.user_id, Q2Answer.answer, DailySession.day)
            .join(DailySession, DailySession.id == Q2Answer.session_id)
            .where(
                Q2Answer.chat_id == chat_id,
                DailySession.chat_id == chat_id,
                DailySession.day >= start,
                DailySession.day <= end,
            )
        )
        q2res = await session.execute(q2q)
        q2_rows = q2res.all()

        good: dict[int, int] = {}
        ok: dict[int, int] = {}
        bad: dict[int, int] = {}

        for user_id, ans, _day in q2_rows:
            if ans == "good":
                good[user_id] = good.get(user_id, 0) + 1
            elif ans == "ok":
                ok[user_id] = ok.get(user_id, 0) + 1
            elif ans == "bad":
                bad[user_id] = bad.get(user_id, 0) + 1

        # Итоги по чату
        total_poop = sum(poop_counts.values())
        total_no = sum(no_counts.values())
        total_good = sum(good.values())
        total_ok = sum(ok.values())
        total_bad = sum(bad.values())

        total_participants = len(participants)
        total_answered = len(
            {p.user_id for p in participants if p.user_id in answered_users})

        # Тело
        lines = [
            title,
            "",
            "Итог по чату:",
            f"💩 {total_poop} • ❌ {total_no}",
            f"😇 {total_good} • 😐 {total_ok} • 😫 {total_bad}",
            f"Ответили: {total_answered}/{total_participants} участников",
            "",
            "Участники:",
        ]

        for p in participants:
            m = mention_user(p.user_id, p.full_name, p.username)

            poop_n = poop_counts.get(p.user_id, 0)
            no_n = no_counts.get(p.user_id, 0)

            g = good.get(p.user_id, 0)
            o = ok.get(p.user_id, 0)
            b = bad.get(p.user_id, 0)

            streak_days, streak_start = await calc_streak_for_user(session, chat_id, p.user_id, today)
            st = streak_text(streak_days, streak_start)

            lines.append(
                f"- {m} — 💩 {poop_n} • ❌ {no_n} | 😇 {g} • 😐 {o} • 😫 {b} | {st}")

        return "\n".join(lines)


# ---------------------------
# Core app helpers
# ---------------------------

async def ensure_chat_saved(chat_id: int, chat_type: str, title: str | None) -> None:
    async with SessionMaker() as session:
        existing = await session.get(Chat, chat_id)
        if existing:
            existing.chat_type = chat_type
            existing.title = title
            existing.is_enabled = True
        else:
            session.add(Chat(chat_id=chat_id, chat_type=chat_type,
                        title=title, is_enabled=True))
        await session.commit()


async def upsert_participant(chat_id: int, user_id: int, username: str | None, full_name: str) -> None:
    async with SessionMaker() as session:
        q = Participant.__table__.select().where(
            (Participant.chat_id == chat_id) & (Participant.user_id == user_id)
        )
        res = await session.execute(q)
        row = res.first()

        if row:
            await session.execute(
                Participant.__table__.update()
                .where((Participant.chat_id == chat_id) & (Participant.user_id == user_id))
                .values(username=username, full_name=full_name, is_opted_out=False)
            )
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


async def build_q1_text(chat_id: int) -> str:
    day = today_local_date()

    async with SessionMaker() as session:
        sess = await get_or_create_session(session, chat_id, day)
        participants = await get_active_participants(session, chat_id)

        if len(participants) == 0:
            return render_question1_empty(fmt_day(day))

        answers = await get_q1_answers_map(session, sess.id)

        lines = []
        for p in participants:
            ans = answers.get(p.user_id)
            answer_val = ans[0] if ans else None
            remind_at = ans[1] if ans else None

            streak_days, streak_start = await calc_streak_for_user(session, chat_id, p.user_id, day)

            m = mention_user(p.user_id, p.full_name, p.username)
            st = status_text(answer_val, remind_at)
            st_text = f"{st} • {streak_text(streak_days, streak_start)}"
            lines.append((m, st_text))

        return render_q1(day, lines)


async def build_q2_text(chat_id: int, sess_id: int) -> str:
    day = today_local_date()

    async with SessionMaker() as session:
        poop_ids = await get_q1_poop_user_ids(session, sess_id)
        if not poop_ids:
            return ""

        parts = await get_active_participants(session, chat_id)
        parts = [p for p in parts if p.user_id in poop_ids]

        answers = await get_q2_answers_map(session, sess_id)

        lines = []
        for p in parts:
            m = mention_user(p.user_id, p.full_name, p.username)
            st = status_q2_text(answers.get(p.user_id))
            lines.append((m, st))

        return render_q2(day, lines)


async def reminders_tick(bot: Bot) -> None:
    try:
        await run_due_reminders(bot)
    except Exception:
        logging.exception("Reminder tick failed")


async def daily_post_tick(bot: Bot) -> None:
    try:
        await post_daily_q1(bot, build_q1_text, kb_question1)
    except Exception:
        logging.exception("Daily post tick failed")


async def closer_tick(bot: Bot) -> None:
    try:
        await close_today_sessions(bot)
    except Exception:
        logging.exception("Close session tick failed")


# ---------------------------
# Main
# ---------------------------

async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing in environment (.env)")

    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminders_tick, "interval", seconds=30, args=[bot])
    scheduler.add_job(closer_tick, "interval", seconds=30, args=[bot])
    scheduler.add_job(daily_post_tick, "cron", hour=15, minute=0, args=[bot])
    scheduler.start()
    logging.info("Scheduler started")

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
                "Start: first Q1 created without reply: chat_id=%s msg_id=%s", chat.id, sent.message_id)
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
                logging.exception(
                    "Start: failed to edit existing Q1 (bad request)")
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

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
