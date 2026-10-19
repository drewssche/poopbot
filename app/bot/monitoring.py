import asyncio
import logging
import time

from aiogram.methods import GetUpdates
from aiogram.exceptions import TelegramNetworkError


class PollingActivityMiddleware:
    def __init__(self, on_success):
        self.on_success = on_success

    async def __call__(self, make_request, bot, method):
        result = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            # Empty successful polls are healthy too: a quiet chat is not a failure.
            self.on_success()
        return result


async def telegram_guard_loop(bot, settings, get_last_received_ts):
    logger = logging.getLogger(__name__)
    failures = 0
    last_webhook_check = time.monotonic()
    interval = max(5, min(settings.polling_guard_interval_sec, settings.webhook_guard_interval_sec))
    while True:
        await asyncio.sleep(interval)
        now = time.monotonic()
        idle = now - get_last_received_ts() >= settings.heartbeat_stale_sec
        webhook_due = settings.webhook_guard_enabled and now - last_webhook_check >= settings.webhook_guard_interval_sec
        if not idle and not webhook_due:
            failures = 0
            continue
        try:
            info = await asyncio.wait_for(bot.get_webhook_info(), settings.polling_guard_request_timeout_sec)
            last_webhook_check = now
            if info.url:
                if settings.webhook_guard_enabled:
                    logger.warning('Webhook detected during polling; resetting without dropping updates')
                    await asyncio.wait_for(bot.delete_webhook(drop_pending_updates=False), settings.polling_guard_request_timeout_sec)
                else:
                    logger.warning('Webhook detected while polling is enabled')
            if failures:
                logger.info('Telegram connectivity recovered')
            failures = 0
            if info.url:
                continue
        except (TelegramNetworkError, asyncio.TimeoutError) as error:
            failures += 1
            logger.warning('Telegram connectivity failure %s/%s', failures, settings.polling_guard_network_failures_to_restart)
            if failures >= settings.polling_guard_network_failures_to_restart:
                raise RuntimeError('Repeated Telegram connectivity failures') from error
            continue
        except Exception:
            logger.exception('Telegram guard check failed')
            continue
        if (idle and settings.polling_guard_enabled and settings.polling_guard_pending_threshold > 0
                and info.pending_update_count >= settings.polling_guard_pending_threshold):
            raise RuntimeError('Polling stalled with pending Telegram updates')
