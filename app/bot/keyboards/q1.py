from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def q1_keyboard(has_any_members: bool, show_remind: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if has_any_members:
        kb.row(
            InlineKeyboardButton(text="-💩", callback_data="q1:minus"),
            InlineKeyboardButton(text="+💩", callback_data="q1:plus"),
        )
    else:
        kb.row(InlineKeyboardButton(text="+💩", callback_data="q1:plus"))

    return kb.as_markup()
