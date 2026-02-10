from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def help_root_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"help:settings:{owner_id}"))
    kb.row(InlineKeyboardButton(text="🤖 О боте", callback_data=f"help:about:{owner_id}"))
    return kb.as_markup()


def help_settings_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🗑️ Удалить меня", callback_data=f"help:delete_me:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⏱️ Установить время", callback_data=f"help:set_time:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:back:{owner_id}"))
    return kb.as_markup()


def help_time_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🌅 Утро (10:00)", callback_data=f"help:time:10:{owner_id}"))
    kb.row(InlineKeyboardButton(text="🍽️ Обед (14:00)", callback_data=f"help:time:14:{owner_id}"))
    kb.row(InlineKeyboardButton(text="🌙 Вечер (19:00)", callback_data=f"help:time:19:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:back:{owner_id}"))
    return kb.as_markup()


def help_delete_confirm_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"help:delete_confirm:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:settings:{owner_id}"))
    return kb.as_markup()
