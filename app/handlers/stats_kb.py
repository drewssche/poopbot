from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_stats(selected: str = "today") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    def label(code: str, text: str) -> str:
        return f"• {text}" if code == selected else text

    kb.row(InlineKeyboardButton(text=label(
        "today", "Сегодня"), callback_data="stats:today"))
    kb.row(InlineKeyboardButton(text=label(
        "week", "На этой неделе"), callback_data="stats:week"))
    kb.row(InlineKeyboardButton(text=label(
        "month", "В этом месяце"), callback_data="stats:month"))
    kb.row(InlineKeyboardButton(text=label(
        "year", "В этом году"), callback_data="stats:year"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back"))
    return kb.as_markup()
