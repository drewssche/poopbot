from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def q1_keyboard(
    has_any_members: bool,
    show_q2_q3_button: bool = False,
    show_restore_streak_button: bool = False,
) -> InlineKeyboardMarkup:
    from app.services.q1_markup import q1_markup
    return InlineKeyboardMarkup(**q1_markup(has_any_members, show_q2_q3_button, show_restore_streak_button))


def closed_q1_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💩 Забыл отметиться", callback_data="q1:forgot"),
    ]])
