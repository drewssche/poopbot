import asyncio
import logging
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict
from aiogram import Bot, Dispatcher
from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.event.bases import UNHANDLED

from app.core.config import Settings
from app.db.runtime import configure_database, close_database
from app.bot.monitoring import PollingActivityMiddleware, telegram_guard_loop
from app.services.scheduler_service import recover_missing_q1_on_startup, start_scheduler

from app.bot.handlers.commands import router as commands_router
from app.bot.handlers.callbacks_q1 import router as callbacks_q1_router
from app.bot.handlers.callbacks_q2 import router as callbacks_q2_router
from app.bot.handlers.callbacks_q3 import router as callbacks_q3_router
from app.bot.handlers.callbacks_restore import router as callbacks_restore_router
from app.bot.handlers.callbacks_restore_preview import router as callbacks_restore_preview_router
from app.bot.handlers.callbacks_streak_admin import router as callbacks_streak_admin_router
from app.bot.handlers.callbacks_help import router as callbacks_help_router
from app.bot.handlers.callbacks_recap import router as callbacks_recap_router
from app.bot.handlers.callbacks_stats import router as callbacks_stats_router
from app.bot.handlers.callbacks_debug import router as callbacks_debug_router
from app.bot.handlers.callbacks_fallback import router as callbacks_fallback_router


class _UpdateActivityMiddleware(BaseMiddleware):
    def __init__(
        self,
        on_received: Callable[[], None],
        on_handled: Callable[[], None],
    ) -> None:
        self._on_received = on_received
        self._on_handled = on_handled

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        self._on_received()
        response = await handler(event, data)
        if response is not UNHANDLED:
            self._on_handled()
        return response


async def _heartbeat_loop(
    interval_sec: int,
    stale_sec: int,
    get_last_received_ts: Callable[[], float],
    get_last_handled_ts: Callable[[], float],
) -> None:
    logger = logging.getLogger(__name__)
    warned = False
    while True:
        await asyncio.sleep(interval_sec)
        received_idle_sec = int(time.monotonic() - get_last_received_ts())
        handled_idle_sec = int(time.monotonic() - get_last_handled_ts())
        if received_idle_sec < stale_sec and handled_idle_sec >= stale_sec:
            if not warned:
                logger.warning("Updates arrive but none are handled for %ss", handled_idle_sec)
            warned = True
        else:
            warned = False
            logger.debug("Heartbeat ok, last update %ss ago", received_idle_sec)


async def _heartbeat_file_loop(
    interval_sec: int,
    get_last_received_ts: Callable[[], float],
    get_last_handled_ts: Callable[[], float],
    get_last_poll_success_ts: Callable[[], float],
) -> None:
    path = Path(tempfile.gettempdir()) / "poopbot_heartbeat.json"
    logger = logging.getLogger(__name__)
    while True:
        try:
            now = datetime.now(timezone.utc).isoformat()
            payload = json.dumps(dict(ts_utc=now,
                last_received_sec=int(time.monotonic() - get_last_received_ts()),
                last_handled_sec=int(time.monotonic() - get_last_handled_ts()),
                last_poll_success_sec=int(time.monotonic() - get_last_poll_success_ts())))
            temporary = path.with_suffix('.tmp')
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        except Exception:
            logger.exception("Failed to write heartbeat file %s", path)
        await asyncio.sleep(max(5, interval_sec))


async def _handled_rate_loop(
    interval_sec: int,
    get_total_received: Callable[[], int],
    get_total_handled: Callable[[], int],
) -> None:
    logger = logging.getLogger(__name__)
    prev_received = get_total_received()
    prev_handled = get_total_handled()
    while True:
        await asyncio.sleep(interval_sec)
        total_received = get_total_received()
        total_handled = get_total_handled()
        delta_received = max(0, total_received - prev_received)
        delta_handled = max(0, total_handled - prev_handled)
        prev_received = total_received
        prev_handled = total_handled
        if delta_received == 0:
            logger.info("Handled rate: no updates in last %ss", interval_sec)
            continue
        rate = (delta_handled / delta_received) * 100.0
        logger.info(
            "Handled rate: %s/%s (%.1f%%) for last %ss",
            delta_handled,
            delta_received,
            rate,
            interval_sec,
        )


async def run_bot(settings: Settings) -> None:
    session_factory = configure_database(settings)
    bot = None
    try:
        bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        await _run_bot(settings, bot, session_factory)
    finally:
        try:
            if bot is not None:
                await bot.session.close()
        finally:
            close_database()


async def _run_bot(settings, bot, session_factory):
    dp = Dispatcher()
    dp.include_router(commands_router)
    dp.include_router(callbacks_q1_router)
    dp.include_router(callbacks_q2_router)
    dp.include_router(callbacks_q3_router)
    dp.include_router(callbacks_restore_router)
    dp.include_router(callbacks_restore_preview_router)
    dp.include_router(callbacks_streak_admin_router)
    dp.include_router(callbacks_help_router)
    dp.include_router(callbacks_recap_router)
    dp.include_router(callbacks_stats_router)
    dp.include_router(callbacks_debug_router)
    dp.include_router(callbacks_fallback_router)

    if settings.startup_delete_webhook:
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates_on_start)

    last_received_ts = time.monotonic()
    last_handled_ts = time.monotonic()
    total_received_count = 0
    total_handled_count = 0

    last_poll_success_ts = time.monotonic()

    def _touch_poll_success():
        nonlocal last_poll_success_ts
        last_poll_success_ts = time.monotonic()

    bot.session.middleware(PollingActivityMiddleware(_touch_poll_success))

    def _touch_received() -> None:
        nonlocal last_received_ts, total_received_count
        last_received_ts = time.monotonic()
        total_received_count += 1

    def _touch_handled() -> None:
        nonlocal last_handled_ts, total_handled_count
        last_handled_ts = time.monotonic()
        total_handled_count += 1

    dp.update.outer_middleware(_UpdateActivityMiddleware(_touch_received, _touch_handled))

    if settings.startup_recover_missing_q1:
        await recover_missing_q1_on_startup(
            bot,
            session_factory,
            chat_throttle_sec=settings.scheduler_chat_throttle_sec,
        )

    scheduler = start_scheduler(
        bot,
        session_factory,
        chat_throttle_sec=settings.scheduler_chat_throttle_sec,
        tick_interval_sec=settings.scheduler_tick_interval_sec,
        q1_catchup_max_delay_min=settings.scheduler_q1_catchup_max_delay_min,
    )

    hb_task = asyncio.create_task(
        _heartbeat_loop(
            interval_sec=settings.heartbeat_interval_sec,
            stale_sec=settings.heartbeat_stale_sec,
            get_last_received_ts=lambda: last_received_ts,
            get_last_handled_ts=lambda: last_handled_ts,
        )
    )
    heartbeat_file_task = asyncio.create_task(
        _heartbeat_file_loop(settings.heartbeat_interval_sec,
            lambda: last_received_ts, lambda: last_handled_ts, lambda: last_poll_success_ts)
    )
    handled_rate_task = (
        asyncio.create_task(
            _handled_rate_loop(
                interval_sec=max(30, settings.handled_rate_log_interval_sec),
                get_total_received=lambda: total_received_count,
                get_total_handled=lambda: total_handled_count,
            )
        )
        if settings.handled_rate_log_interval_sec > 0
        else None
    )
    connectivity_guard_task = asyncio.create_task(
        telegram_guard_loop(bot, settings, lambda: last_received_ts)
    )

    logging.getLogger(__name__).info("Bot started")
    polling_task = asyncio.create_task(dp.start_polling(bot, close_bot_session=False))
    tasks = [polling_task, connectivity_guard_task, hb_task, heartbeat_file_task]
    if handled_rate_task is not None:
        tasks.append(handled_rate_task)
    try:
        done, _ = await asyncio.wait({polling_task, connectivity_guard_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        scheduler.shutdown(wait=False)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
