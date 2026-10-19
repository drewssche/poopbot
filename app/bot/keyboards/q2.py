from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from app.services.q2_q3_markup import BRISTOL_CHOICES, q2_markup


def q2_keyboard(selected_choice: str | None = None, *, private_flow: bool = False,
                  event_n: int | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(**q2_markup(selected_choice, private_flow=private_flow, event_n=event_n))
