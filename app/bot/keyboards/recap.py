from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def recap_next_kb(source_chat_id: int, year: int, next_index: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="Следующая",
            callback_data=f"recap:next:{source_chat_id}:{year}:{next_index}",
        )
    )
    return kb.as_markup()


def recap_chat_card_kb(source_chat_id: int, year: int, next_index: int, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_next:
        kb.row(
            InlineKeyboardButton(
                text="Следующая",
                callback_data=f"recap:chatnext:{source_chat_id}:{year}:{next_index}",
            )
        )
    return kb.as_markup()


def recap_announce_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎉 Рекап года", callback_data="stats:open:recap"))
    return kb.as_markup()


def recap_entry_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Рекап чата", callback_data="recap:entry:chat"))
    kb.row(InlineKeyboardButton(text="🎉 Личный рекап", callback_data="recap:entry:personal"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="stats:back:root"))
    return kb.as_markup()


def recap_chat_pick_mode_kb(year: int, mode: str, chat_options: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for chat_id, title in chat_options:
        kb.row(
            InlineKeyboardButton(
                text=title[:48],
                callback_data=f"recap:pick:{mode}:{chat_id}:{year}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ К выбору", callback_data="recap:entry:menu"))
    return kb.as_markup()
