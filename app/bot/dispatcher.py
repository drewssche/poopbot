import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.core.config import Settings
from app.db.engine import make_engine, make_session_factory
from app.services.scheduler_service import start_scheduler

from app.bot.handlers.commands import router as commands_router
from app.bot.handlers.callbacks_q1 import router as callbacks_q1_router
from app.bot.handlers.callbacks_q2 import router as callbacks_q2_router
from app.bot.handlers.callbacks_q3 import router as callbacks_q3_router
from app.bot.handlers.callbacks_help import router as callbacks_help_router
from app.bot.handlers.callbacks_recap import router as callbacks_recap_router
from app.bot.handlers.callbacks_stats import router as callbacks_stats_router


async def run_bot(settings: Settings) -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    dp = Dispatcher()
    dp.include_router(commands_router)
    dp.include_router(callbacks_q1_router)
    dp.include_router(callbacks_q2_router)
    dp.include_router(callbacks_q3_router)
    dp.include_router(callbacks_help_router)
    dp.include_router(callbacks_recap_router)
    dp.include_router(callbacks_stats_router)

    start_scheduler(bot, session_factory)

    logging.getLogger(__name__).info("Bot started")
    await dp.start_polling(bot)
