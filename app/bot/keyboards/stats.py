from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

SCOPE_MY = "my"
SCOPE_CHAT = "chat"
SCOPE_AMONG = "among"
SCOPE_GLOBAL = "global"
SCOPE_RECAP = "recap"

PERIOD_TODAY = "today"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"
PERIOD_ALL = "all"


def _mark(label: str, active: bool) -> str:
    return f"• {label}" if active else label


def stats_root_kb(show_recap: bool = False, is_private_chat: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🙋‍♂️ Моя", callback_data=f"stats:open:{SCOPE_MY}"))
    if not is_private_chat:
        kb.row(InlineKeyboardButton(text="👥 В этом чате", callback_data=f"stats:open:{SCOPE_CHAT}"))
    kb.row(InlineKeyboardButton(text="🏟️ Среди чатов", callback_data=f"stats:open:{SCOPE_AMONG}"))
    kb.row(InlineKeyboardButton(text="🌍 Глобальная", callback_data=f"stats:open:{SCOPE_GLOBAL}"))
    if show_recap:
        kb.row(InlineKeyboardButton(text="🎉 Рекап года", callback_data=f"stats:open:{SCOPE_RECAP}"))
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


def stats_global_kb(is_private_chat: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not is_private_chat:
        kb.row(InlineKeyboardButton(text="👤 Показать меня", callback_data="stats:global:me"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()


def stats_among_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()
