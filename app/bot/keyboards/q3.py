from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


FEELING_CHOICES: list[tuple[str, str]] = [
    ("great", "\U0001F607 \u041f\u0440\u0435\u043a\u0440\u0430\u0441\u043d\u043e"),
    ("ok", "\U0001F610 \u0421\u043e\u0439\u0434\u0451\u0442"),
    ("bad", "\U0001F62B \u0423\u0436\u0430\u0441\u043d\u043e"),
]

def _choice_label(text: str, selected: bool) -> str:
    _ = selected
    return text


def q3_keyboard(
    selected_choice: str | None = None,
    *,
    private_flow: bool = False,
    event_n: int | None = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    target_n = int(event_n or 1)
    for choice, text in FEELING_CHOICES:
        cb = f"q3:set:{target_n}:{choice}" if private_flow else f"q3:{choice}"
        kb.row(
            InlineKeyboardButton(
                text=_choice_label(text, selected_choice == choice),
                callback_data=cb,
            )
        )

    if private_flow:
        kb.row(
            InlineKeyboardButton(text="⬅️ Вернуться", callback_data=f"q3:back_q2:{target_n}"),
            InlineKeyboardButton(text="Пропустить", callback_data=f"q3:skip:{target_n}"),
        )

    return kb.as_markup()
