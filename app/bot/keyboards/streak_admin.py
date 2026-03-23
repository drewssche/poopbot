from __future__ import annotations

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def streak_admin_kb(target_date: date) -> InlineKeyboardMarkup:
    prev_date = (target_date - timedelta(days=1)).isoformat()
    next_date = (target_date + timedelta(days=1)).isoformat()
    current_date = target_date.isoformat()

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="⬅️ День раньше", callback_data=f"streakadmin:date:{prev_date}"),
        InlineKeyboardButton(text="День позже ➡️", callback_data=f"streakadmin:date:{next_date}"),
    )
    kb.row(InlineKeyboardButton(text="📨 Отправить во все группы", callback_data=f"streakadmin:send:groups:{current_date}"))
    kb.row(InlineKeyboardButton(text="💬 Отправить во все личные", callback_data=f"streakadmin:send:private:{current_date}"))
    kb.row(InlineKeyboardButton(text="📊 Показать статус", callback_data=f"streakadmin:status:{current_date}"))
    return kb.as_markup()
