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


def stats_root_kb(show_recap: bool = False, is_private_chat: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🙋 Моя", callback_data=f"stats:open:{SCOPE_MY}"))
    if is_private_chat:
        kb.row(InlineKeyboardButton(text="💬 В этой личке", callback_data=f"stats:open:{SCOPE_CHAT}"))
    else:
        kb.row(InlineKeyboardButton(text="👥 В этом чате", callback_data=f"stats:open:{SCOPE_CHAT}"))
    kb.row(InlineKeyboardButton(text="🏟️ Среди чатов", callback_data=f"stats:open:{SCOPE_AMONG}"))
    kb.row(InlineKeyboardButton(text="🌍 Глобальная", callback_data=f"stats:open:{SCOPE_GLOBAL}"))
    if show_recap:
        kb.row(InlineKeyboardButton(text="🎉 Рекап года", callback_data=f"stats:open:{SCOPE_RECAP}"))
    return kb.as_markup()


def stats_local_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
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
