from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def q2_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🧱 1–2 (жёстко / сухо)", callback_data="q2:12"))
    kb.row(InlineKeyboardButton(text="🍌 3–4 (норма)", callback_data="q2:34"))
    kb.row(InlineKeyboardButton(text="🍦 5–6 (мягко)", callback_data="q2:56"))
    kb.row(InlineKeyboardButton(text="💦 7 (водичка)", callback_data="q2:7"))
    return kb.as_markup()
