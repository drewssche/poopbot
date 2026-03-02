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


def stats_local_kb(scope: str = SCOPE_CHAT) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📅 Неделя", callback_data=f"stats:period:{scope}:{PERIOD_WEEK}:0"),
        InlineKeyboardButton(text="🗓 Месяц", callback_data=f"stats:period:{scope}:{PERIOD_MONTH}:0"),
        InlineKeyboardButton(text="🧾 Год", callback_data=f"stats:period:{scope}:{PERIOD_YEAR}:0"),
    )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()


def stats_global_kb(is_private_chat: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📅 Неделя", callback_data=f"stats:period:{SCOPE_GLOBAL}:{PERIOD_WEEK}:0"),
        InlineKeyboardButton(text="🗓 Месяц", callback_data=f"stats:period:{SCOPE_GLOBAL}:{PERIOD_MONTH}:0"),
        InlineKeyboardButton(text="🧾 Год", callback_data=f"stats:period:{SCOPE_GLOBAL}:{PERIOD_YEAR}:0"),
    )
    if not is_private_chat:
        kb.row(InlineKeyboardButton(text="👤 Показать меня", callback_data="stats:global:me"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()


def stats_among_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📅 Неделя", callback_data=f"stats:period:{SCOPE_AMONG}:{PERIOD_WEEK}:0"),
        InlineKeyboardButton(text="🗓 Месяц", callback_data=f"stats:period:{SCOPE_AMONG}:{PERIOD_MONTH}:0"),
        InlineKeyboardButton(text="🧾 Год", callback_data=f"stats:period:{SCOPE_AMONG}:{PERIOD_YEAR}:0"),
    )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()


def stats_period_kb(scope: str, period: str, offset: int, is_private_chat: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    prev_offset = int(offset) + 1
    next_offset = max(0, int(offset) - 1)

    kb.row(
        InlineKeyboardButton(text="⬅️ Раньше", callback_data=f"stats:period:{scope}:{period}:{prev_offset}"),
        InlineKeyboardButton(
            text="➡️ Ближе",
            callback_data=f"stats:period:{scope}:{period}:{next_offset}" if int(offset) > 0 else "stats:noop",
        ),
    )

    kb.row(
        InlineKeyboardButton(text="📅 Неделя", callback_data=f"stats:period:{scope}:{PERIOD_WEEK}:0"),
        InlineKeyboardButton(text="🗓 Месяц", callback_data=f"stats:period:{scope}:{PERIOD_MONTH}:0"),
        InlineKeyboardButton(text="🧾 Год", callback_data=f"stats:period:{scope}:{PERIOD_YEAR}:0"),
    )

    if scope == SCOPE_GLOBAL and not is_private_chat:
        kb.row(InlineKeyboardButton(text="👤 Показать меня", callback_data="stats:global:me"))

    kb.row(InlineKeyboardButton(text="↩️ За всё время", callback_data=f"stats:open:{scope}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()
