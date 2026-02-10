from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def q3_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="😇 Прекрасно", callback_data="q3:great"))
    kb.row(InlineKeyboardButton(text="😐 Сойдёт", callback_data="q3:ok"))
    kb.row(InlineKeyboardButton(text="😫 Ужасно", callback_data="q3:bad"))
    return kb.as_markup()
