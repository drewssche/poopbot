from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

logger = logging.getLogger(__name__)
_TELEGRAM_CALL_TIMEOUT_SEC = 15.0


async def safe_sleep_on_retry(exc: Exception) -> bool:
    if not isinstance(exc, TelegramRetryAfter):
        return False
    retry_after = exc.retry_after
    try:
        delay = float(retry_after) + 0.5
    except Exception:
        return False
    delay = max(0.5, min(delay, 60.0))
    logger.warning("Telegram rate limit hit. Sleeping %.1fs", delay)
    await asyncio.sleep(delay)
    return True


async def safe_send_message(bot: Bot, **kwargs):
    for _ in range(3):
        try:
            return await asyncio.wait_for(
                bot.send_message(**kwargs),
                timeout=_TELEGRAM_CALL_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("send_message timeout after %.1fs", _TELEGRAM_CALL_TIMEOUT_SEC)
            continue
        except Exception as e:
            if await safe_sleep_on_retry(e):
                continue
            raise
    raise TimeoutError("send_message failed after retries")


async def safe_edit_message_text(bot: Bot, **kwargs):
    for _ in range(3):
        try:
            return await asyncio.wait_for(
                bot.edit_message_text(**kwargs),
                timeout=_TELEGRAM_CALL_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("edit_message_text timeout after %.1fs", _TELEGRAM_CALL_TIMEOUT_SEC)
            continue
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                return None
            if "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                return None
            raise
        except Exception as e:
            if await safe_sleep_on_retry(e):
                continue
            raise
    raise TimeoutError("edit_message_text failed after retries")
