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
        InlineKeyboardButton(text="⬅️ Дата -1 день", callback_data=f"streakadmin:date:{prev_date}"),
        InlineKeyboardButton(text="Дата +1 день ➡️", callback_data=f"streakadmin:date:{next_date}"),
    )
    kb.row(InlineKeyboardButton(text="🎯 Взять топ-кандидат", callback_data="streakadmin:date:auto"))
    kb.row(InlineKeyboardButton(text="👀 Превью в личку", callback_data=f"streakadmin:preview:{current_date}"))
    kb.row(InlineKeyboardButton(text="🧪 Боевое себе в личку", callback_data=f"streakadmin:battle:{current_date}"))
    kb.row(InlineKeyboardButton(text="↩️ Отменить восстановление себе", callback_data=f"streakadmin:undo:{current_date}"))
    kb.row(InlineKeyboardButton(text="📨 Отправить во все группы", callback_data=f"streakadmin:send:groups:{current_date}"))
    kb.row(InlineKeyboardButton(text="💬 Отправить во все личные", callback_data=f"streakadmin:send:private:{current_date}"))
    kb.row(InlineKeyboardButton(text="📊 Показать статус", callback_data=f"streakadmin:status:{current_date}"))
    return kb.as_markup()
