from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def q1_keyboard(
    has_any_members: bool,
    show_remind: bool = True,
    show_q2_q3_button: bool = False,
    show_restore_streak_button: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if has_any_members:
        kb.row(
            InlineKeyboardButton(text="-💩", callback_data="q1:minus"),
            InlineKeyboardButton(text="+💩", callback_data="q1:plus"),
        )
    else:
        kb.row(InlineKeyboardButton(text="+💩", callback_data="q1:plus"))

    if show_q2_q3_button:
        kb.row(InlineKeyboardButton(text="🧻 Уточняющие вопросы", callback_data="q1:q2q3"))

    if show_restore_streak_button:
        kb.row(InlineKeyboardButton(text="♻️ Вернуть стрик", callback_data="q1:restore_streak"))

    return kb.as_markup()
