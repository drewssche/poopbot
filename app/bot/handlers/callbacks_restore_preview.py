from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

router = Router()


@router.callback_query(F.data.startswith("restorepreview:"))
async def restore_preview_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None:
        return

    action = cb.data.split(":")[1]
    try:
        if action == "noop":
            await cb.answer("Это превью. В боевой рассылке кнопка будет рабочей.", show_alert=False)
            return
        if action == "delete":
            await cb.message.delete()
            await cb.answer("Превью удалено", show_alert=False)
            return
    except TelegramBadRequest:
        raise
