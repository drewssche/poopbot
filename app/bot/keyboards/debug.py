from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def debug_kb(mode: str = "preview") -> InlineKeyboardMarkup:
    is_preview = mode == "preview"
    mode_button_text = "Режим: Превью ✅" if is_preview else "Режим: Отправка ✅"
    mode_toggle = "send" if is_preview else "preview"

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=mode_button_text, callback_data=f"debug:mode:{mode_toggle}"))
    kb.row(InlineKeyboardButton(text="🔄 Обновить меню", callback_data=f"debug:refresh:{mode}"))
    kb.row(InlineKeyboardButton(text="🧪 Q1 автопост", callback_data=f"debug:run:{mode}:q1"))
    kb.row(InlineKeyboardButton(text="🧪 Q2/Q3", callback_data=f"debug:run:{mode}:q2q3"))
    kb.row(InlineKeyboardButton(text="🧪 Напоминалка 22:00", callback_data=f"debug:run:{mode}:r22"))
    kb.row(InlineKeyboardButton(text="🧪 Финалка 23:30", callback_data=f"debug:run:{mode}:late"))
    kb.row(InlineKeyboardButton(text="🧪 Итоги недели", callback_data=f"debug:run:{mode}:week"))
    kb.row(InlineKeyboardButton(text="🧪 Итоги месяца", callback_data=f"debug:run:{mode}:month"))
    kb.row(InlineKeyboardButton(text="🧪 Итоги года", callback_data=f"debug:run:{mode}:year"))
    kb.row(InlineKeyboardButton(text="🧪 Holiday 9 Feb", callback_data=f"debug:run:{mode}:holiday:feb9"))
    kb.row(InlineKeyboardButton(text="🧪 Holiday 19 Nov", callback_data=f"debug:run:{mode}:holiday:nov19"))
    kb.row(InlineKeyboardButton(text="🧪 Анонс рекапа", callback_data=f"debug:run:{mode}:recap_announce"))
    kb.row(InlineKeyboardButton(text="🧪 Рекап чата", callback_data=f"debug:run:{mode}:recap_chat"))
    kb.row(InlineKeyboardButton(text="🧪 Рекап личный (текущий чат)", callback_data=f"debug:run:{mode}:recap_my_chat"))
    kb.row(InlineKeyboardButton(text="🧪 Рекап личный (все чаты)", callback_data=f"debug:run:{mode}:recap_my_all"))
    kb.row(InlineKeyboardButton(text="🚀 Прогнать все", callback_data=f"debug:run:{mode}:all"))
    return kb.as_markup()
