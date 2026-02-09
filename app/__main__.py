import os
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.init_db import init_db
from app.handlers import register_handlers
from app.services.question1 import kb_question1
from app.services.reminder_runner import run_due_reminders
from app.services.session_closer import close_today_sessions
from app.services.daily_poster import post_daily_q1
from app.services.text_builders import build_q1_text

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")


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

    register_handlers(dp, bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
