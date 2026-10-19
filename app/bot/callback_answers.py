from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


async def answer_callback(cb: CallbackQuery, text: str | None = None, *, show_alert: bool = False) -> None:
    """A failed Telegram acknowledgement must not interrupt persisted work."""
    try:
        await cb.answer(text, show_alert=show_alert)
    except TelegramAPIError as exc:
        logger.warning(
            "Callback acknowledgement failed chat_id=%s user_id=%s action=%s reason=%s",
            cb.message.chat.id if cb.message else None,
            cb.from_user.id,
            cb.data,
            type(exc).__name__,
        )
