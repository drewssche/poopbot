from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def streak_restore_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="♻️ Восстановить стрик за 7 дней", callback_data="restore:claim"))
    return kb.as_markup()


def streak_restore_preview_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="♻️ Восстановить стрик за 7 дней", callback_data="restorepreview:noop"))
    kb.row(InlineKeyboardButton(text="🗑 Убрать превью", callback_data="restorepreview:delete"))
    return kb.as_markup()
