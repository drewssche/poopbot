import asyncio
import contextlib
import logging
import os
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
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest

from app.core.config import Settings
from app.db.engine import make_engine, make_session_factory
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
    while True:
        await asyncio.sleep(interval_sec)
        received_idle_sec = int(time.monotonic() - get_last_received_ts())
        handled_idle_sec = int(time.monotonic() - get_last_handled_ts())
        if received_idle_sec >= stale_sec:
            logger.warning(
                "No updates received for %ss (possible token/webhook/competing-instance issue)",
                received_idle_sec,
            )
        elif handled_idle_sec >= stale_sec:
            logger.warning(
                "No handled updates for %ss (updates still coming, check stale buttons/handlers)",
                handled_idle_sec,
            )
        else:
            logger.debug(
                "Heartbeat ok, last update %ss ago, last handled %ss ago",
                received_idle_sec,
                handled_idle_sec,
            )


async def _webhook_guard_loop(bot: Bot, interval_sec: int) -> None:
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval_sec)
        try:
            info = await bot.get_webhook_info()
            if info.url:
                logger.warning("Webhook detected during polling, resetting webhook url=%s", info.url)
                await bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            logger.exception("Webhook guard check failed")


async def _polling_guard_loop(
    bot: Bot,
    interval_sec: int,
    stale_sec: int,
    pending_threshold: int,
    get_last_received_ts: Callable[[], float],
) -> None:
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval_sec)
        try:
            idle_sec = int(time.monotonic() - get_last_received_ts())
            if idle_sec < stale_sec:
                continue
            info = await bot.get_webhook_info()
            if info.pending_update_count >= pending_threshold:
                logger.critical(
                    "Polling guard restart: idle=%ss pending_updates=%s threshold=%s",
                    idle_sec,
                    info.pending_update_count,
                    pending_threshold,
                )
                os._exit(1)
        except Exception:
            logger.exception("Polling guard check failed")


async def _heartbeat_file_loop(
    interval_sec: int,
    get_last_received_ts: Callable[[], float],
    get_last_handled_ts: Callable[[], float],
) -> None:
    path = Path(tempfile.gettempdir()) / "poopbot_heartbeat.json"
    logger = logging.getLogger(__name__)
    while True:
        try:
            now = datetime.now(timezone.utc).isoformat()
            payload = (
                "{"
                f"\"ts_utc\":\"{now}\","
                f"\"last_received_sec\":{int(time.monotonic() - get_last_received_ts())},"
                f"\"last_handled_sec\":{int(time.monotonic() - get_last_handled_ts())}"
                "}"
            )
            path.write_text(payload, encoding="utf-8")
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


async def _polling_connectivity_guard_loop(
    bot: Bot,
    interval_sec: int,
    stale_sec: int,
    request_timeout_sec: int,
    network_failures_to_restart: int,
    get_last_received_ts: Callable[[], float],
) -> None:
    logger = logging.getLogger(__name__)
    network_failures = 0
    while True:
        await asyncio.sleep(interval_sec)
        idle_sec = int(time.monotonic() - get_last_received_ts())
        if idle_sec < stale_sec:
            if network_failures > 0:
                logger.info("Polling guard: telegram connectivity recovered")
            network_failures = 0
            continue

        try:
            info = await asyncio.wait_for(bot.get_webhook_info(), timeout=request_timeout_sec)
            if getattr(info, "url", ""):
                logger.warning("Polling guard: webhook is set while polling is enabled (url=%s)", info.url)
            await asyncio.wait_for(bot.get_me(), timeout=request_timeout_sec)
            if network_failures > 0:
                logger.info("Polling guard: telegram connectivity recovered")
            network_failures = 0
        except (TelegramNetworkError, asyncio.TimeoutError) as e:
            network_failures += 1
            logger.error(
                "Polling guard network failure %s/%s after %ss idle: %s",
                network_failures,
                network_failures_to_restart,
                idle_sec,
                e,
            )
            if network_failures >= network_failures_to_restart:
                raise RuntimeError(
                    "Polling guard: restarting process after repeated telegram connectivity failures"
                ) from e
        except TelegramBadRequest as e:
            logger.error("Polling guard telegram bad request after %ss idle: %s", idle_sec, e)
            network_failures = 0
        except Exception as e:
            logger.exception("Polling guard check failed: %s", e)
            network_failures = 0


async def run_bot(settings: Settings) -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    engine = make_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout_sec=settings.db_pool_timeout_sec,
        pool_recycle_sec=settings.db_pool_recycle_sec,
    )
    session_factory = make_session_factory(engine)

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

    start_scheduler(
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
        _heartbeat_file_loop(
            interval_sec=settings.heartbeat_interval_sec,
            get_last_received_ts=lambda: last_received_ts,
            get_last_handled_ts=lambda: last_handled_ts,
        )
    )
    webhook_guard_task = (
        asyncio.create_task(_webhook_guard_loop(bot, settings.webhook_guard_interval_sec))
        if settings.webhook_guard_enabled
        else None
    )
    polling_guard_task = (
        asyncio.create_task(
            _polling_guard_loop(
                bot=bot,
                interval_sec=max(30, settings.polling_guard_interval_sec),
                stale_sec=settings.heartbeat_stale_sec,
                pending_threshold=settings.polling_guard_pending_threshold,
                get_last_received_ts=lambda: last_received_ts,
            )
        )
        if settings.polling_guard_enabled
        else None
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
        _polling_connectivity_guard_loop(
            bot=bot,
            interval_sec=settings.polling_guard_interval_sec,
            stale_sec=settings.heartbeat_stale_sec,
            request_timeout_sec=settings.polling_guard_request_timeout_sec,
            network_failures_to_restart=settings.polling_guard_network_failures_to_restart,
            get_last_received_ts=lambda: last_received_ts,
        )
    )

    logging.getLogger(__name__).info("Bot started")
    try:
        polling_task = asyncio.create_task(dp.start_polling(bot))
        done, pending = await asyncio.wait(
            {polling_task, connectivity_guard_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(Exception):
                await task
        if polling_task in done:
            polling_task.result()
        if connectivity_guard_task in done:
            connectivity_guard_task.result()
    finally:
        hb_task.cancel()
        heartbeat_file_task.cancel()
        if webhook_guard_task is not None:
            webhook_guard_task.cancel()
        if polling_guard_task is not None:
            polling_guard_task.cancel()
        connectivity_guard_task.cancel()
        if handled_rate_task is not None:
            handled_rate_task.cancel()
        with contextlib.suppress(Exception):
            await hb_task
        with contextlib.suppress(Exception):
            await heartbeat_file_task
        if webhook_guard_task is not None:
            with contextlib.suppress(Exception):
                await webhook_guard_task
        if polling_guard_task is not None:
            with contextlib.suppress(Exception):
                await polling_guard_task
        with contextlib.suppress(Exception):
            await connectivity_guard_task
        if handled_rate_task is not None:
            with contextlib.suppress(Exception):
                await handled_rate_task
        with contextlib.suppress(Exception):
            await bot.session.close()
