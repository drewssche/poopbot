from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def q1_keyboard(has_any_members: bool, show_remind: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # row 1
    if has_any_members:
        kb.row(
            InlineKeyboardButton(text="➖💩", callback_data="q1:minus"),
            InlineKeyboardButton(text="➕💩", callback_data="q1:plus"),
        )
    else:
        kb.row(InlineKeyboardButton(text="➕💩", callback_data="q1:plus"))

    # row 2
    if show_remind:
        kb.row(InlineKeyboardButton(text="⏳ Напомнить в 22:00", callback_data="q1:remind"))

    return kb.as_markup()
