from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


GROUP_PAGE_SIZE = 6


def streak_admin_kb(target_date: date) -> InlineKeyboardMarkup:
    current_date = target_date.isoformat()

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👀 Превью в личку", callback_data=f"streakadmin:preview:{current_date}"))
    kb.row(InlineKeyboardButton(text="🧪 Боевое себе в личку", callback_data=f"streakadmin:battle:{current_date}"))
    kb.row(InlineKeyboardButton(text="↩️ Отменить восстановление себе", callback_data=f"streakadmin:undo:{current_date}"))
    kb.row(InlineKeyboardButton(text="🎯 Выбрать группу", callback_data=f"streakadmin:groupmenu:0:{current_date}"))
    kb.row(InlineKeyboardButton(text="📨 Отправить во все группы", callback_data=f"streakadmin:send:groups:{current_date}"))
    kb.row(InlineKeyboardButton(text="💬 Отправить во все личные", callback_data=f"streakadmin:send:private:{current_date}"))
    kb.row(InlineKeyboardButton(text="📊 Показать статус", callback_data=f"streakadmin:status:{current_date}"))
    return kb.as_markup()


def streak_admin_group_picker_kb(target_date: date, group_options: list[tuple[int, str]], page: int) -> InlineKeyboardMarkup:
    current_date = target_date.isoformat()
    total = len(group_options)
    page = max(0, page)
    start = page * GROUP_PAGE_SIZE
    page_items = group_options[start : start + GROUP_PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for chat_id, title in page_items:
        kb.row(
            InlineKeyboardButton(
                text=title[:48],
                callback_data=f"streakadmin:groupsend:{chat_id}:{current_date}",
            )
        )

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"streakadmin:groupmenu:{page - 1}:{current_date}")
        )
    if start + GROUP_PAGE_SIZE < total:
        nav_buttons.append(
            InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"streakadmin:groupmenu:{page + 1}:{current_date}")
        )
    if nav_buttons:
        kb.row(*nav_buttons)

    kb.row(InlineKeyboardButton(text="↩️ Назад в панель", callback_data=f"streakadmin:panel:{current_date}"))
    return kb.as_markup()
