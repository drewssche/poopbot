import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import Bot, Dispatcher
from aiogram import BaseMiddleware
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


class _UpdateActivityMiddleware(BaseMiddleware):
    def __init__(self, on_update: Callable[[], None]) -> None:
        self._on_update = on_update

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        self._on_update()
        return await handler(event, data)


async def _heartbeat_loop(
    interval_sec: int,
    stale_sec: int,
    get_last_update_ts: Callable[[], float],
) -> None:
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval_sec)
        idle_sec = int(time.monotonic() - get_last_update_ts())
        if idle_sec >= stale_sec:
            logger.warning(
                "No handled updates for %ss (possible token/webhook/competing-instance issue)",
                idle_sec,
            )
        else:
            logger.debug("Heartbeat ok, last handled update %ss ago", idle_sec)


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

    if settings.startup_delete_webhook:
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates_on_start)

    last_update_ts = time.monotonic()

    def _touch_update() -> None:
        nonlocal last_update_ts
        last_update_ts = time.monotonic()

    dp.update.outer_middleware(_UpdateActivityMiddleware(_touch_update))

    start_scheduler(bot, session_factory, chat_throttle_sec=settings.scheduler_chat_throttle_sec)

    hb_task = asyncio.create_task(
        _heartbeat_loop(
            interval_sec=settings.heartbeat_interval_sec,
            stale_sec=settings.heartbeat_stale_sec,
            get_last_update_ts=lambda: last_update_ts,
        )
    )

    logging.getLogger(__name__).info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        hb_task.cancel()
        with contextlib.suppress(Exception):
            await hb_task
        with contextlib.suppress(Exception):
            await bot.session.close()
