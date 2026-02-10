from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

SCOPE_MY = "my"
SCOPE_CHAT = "chat"
SCOPE_GLOBAL = "global"

PERIOD_TODAY = "today"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"
PERIOD_ALL = "all"


def _mark(label: str, active: bool) -> str:
    return f"• {label}" if active else label


def stats_root_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🙋‍♂️ Моя", callback_data=f"stats:open:{SCOPE_MY}"))
    kb.row(InlineKeyboardButton(text="👥 В этом чате", callback_data=f"stats:open:{SCOPE_CHAT}"))
    kb.row(InlineKeyboardButton(text="🌍 Глобальная", callback_data=f"stats:open:{SCOPE_GLOBAL}"))
    return kb.as_markup()


def stats_period_kb(scope: str, active_period: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=_mark("📌 Сегодня", active_period == PERIOD_TODAY), callback_data=f"stats:period:{scope}:{PERIOD_TODAY}"))
    kb.row(InlineKeyboardButton(text=_mark("🗓 Неделя", active_period == PERIOD_WEEK), callback_data=f"stats:period:{scope}:{PERIOD_WEEK}"))
    kb.row(InlineKeyboardButton(text=_mark("📅 Месяц", active_period == PERIOD_MONTH), callback_data=f"stats:period:{scope}:{PERIOD_MONTH}"))
    kb.row(InlineKeyboardButton(text=_mark("📆 Год", active_period == PERIOD_YEAR), callback_data=f"stats:period:{scope}:{PERIOD_YEAR}"))
    kb.row(InlineKeyboardButton(text=_mark("♾️ За всё время", active_period == PERIOD_ALL), callback_data=f"stats:period:{scope}:{PERIOD_ALL}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()
