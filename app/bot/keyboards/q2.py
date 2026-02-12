from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


BRISTOL_CHOICES: list[tuple[str, str]] = [
    ("12", "\U0001F9F1 1\u20132 (\u0436\u0451\u0441\u0442\u043a\u043e / \u0441\u0443\u0445\u043e)"),
    ("34", "\U0001F34C 3\u20134 (\u043d\u043e\u0440\u043c\u0430)"),
    ("56", "\U0001F366 5\u20136 (\u043c\u044f\u0433\u043a\u043e)"),
    ("7", "\U0001F4A6 7 (\u0432\u043e\u0434\u0438\u0447\u043a\u0430)"),
]

def _choice_label(text: str, selected: bool) -> str:
    _ = selected
    return text


def q2_keyboard(
    selected_choice: str | None = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for choice, text in BRISTOL_CHOICES:
        kb.row(
            InlineKeyboardButton(
                text=_choice_label(text, selected_choice == choice),
                callback_data=f"q2:{choice}",
            )
        )

    return kb.as_markup()
