from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def streak_restore_keyboard(target_date: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="♻️ Вернуть мой стрик", callback_data=f"restore:claim:{target_date}"))
    return kb.as_markup()
