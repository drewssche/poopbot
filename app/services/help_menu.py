from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_help() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⚙️ Настройки", callback_data="help:settings"))
    kb.row(InlineKeyboardButton(text="📊 Статистика", callback_data="help:stats"))
    kb.row(InlineKeyboardButton(text="❓ Помощь", callback_data="help:about"))
    return kb.as_markup()


def kb_settings(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚫 Не участвовать",
           callback_data=f"set:optout:{owner_id}"))
    kb.row(InlineKeyboardButton(text="🧹 Стереть мои данные",
           callback_data=f"set:wipe:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад",
           callback_data=f"set:back:{owner_id}"))
    return kb.as_markup()
