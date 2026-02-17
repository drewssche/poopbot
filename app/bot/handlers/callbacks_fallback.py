from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query()
async def unknown_callback(cb: CallbackQuery) -> None:
    if cb.from_user is not None and cb.message is not None:
        logger.warning(
            "Unhandled callback chat_id=%s user_id=%s data=%r",
            cb.message.chat.id,
            cb.from_user.id,
            cb.data,
        )
    await cb.answer("Кнопка устарела. Обнови /start или /stats", show_alert=False)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    if message.chat is None or message.from_user is None:
        return
    logger.warning(
        "Unhandled command chat_id=%s user_id=%s text=%r",
        message.chat.id,
        message.from_user.id,
        message.text,
    )
    await message.answer("Не понял команду. Открой /help")
