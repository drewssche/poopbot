from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _mark(label: str, active: bool) -> str:
    return f"• {label}" if active else label


def help_root_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"help:settings:{owner_id}"))
    kb.row(InlineKeyboardButton(text="🤖 О боте", callback_data=f"help:about:{owner_id}"))
    return kb.as_markup()


def help_settings_kb(owner_id: int, is_private_chat: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🗑️ Удалить меня", callback_data=f"help:delete_me:{owner_id}"))
    if not is_private_chat:
        kb.row(InlineKeyboardButton(text="🧹 Удалить меня из этого чата", callback_data=f"help:delete_me_chat:{owner_id}"))
        kb.row(InlineKeyboardButton(text="👁️ Видимость чата в рейтингах", callback_data=f"help:global_vis:{owner_id}"))
    kb.row(InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"help:notifications:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:back:{owner_id}"))
    return kb.as_markup()


def help_notifications_kb(
    owner_id: int,
    current_hour: int | None = None,
    notifications_enabled: bool = True,
    late_reminder_enabled: bool = True,
    q2_q3_enabled: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔔 Уведомления: Вкл" if notifications_enabled else "🔕 Уведомления: Выкл",
            callback_data=f"help:notifications_toggle:{owner_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="⏳ Финалка 23:30: Вкл" if late_reminder_enabled else "⏳ Финалка 23:30: Выкл",
            callback_data=f"help:late_reminder_toggle:{owner_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🧻 Уточняющие вопросы: Вкл" if q2_q3_enabled else "🧻 Уточняющие вопросы: Выкл",
            callback_data=f"help:q2_q3_toggle:{owner_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text=_mark("🌅 Утро (10:00)", current_hour == 10),
            callback_data=f"help:time:10:{owner_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text=_mark("🍽️ Обед (14:00)", current_hour == 14),
            callback_data=f"help:time:14:{owner_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text=_mark("🌙 Вечер (19:00)", current_hour == 19),
            callback_data=f"help:time:19:{owner_id}",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:settings:{owner_id}"))
    return kb.as_markup()


def help_delete_confirm_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"help:delete_confirm_db:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:settings:{owner_id}"))
    return kb.as_markup()


def help_delete_chat_confirm_kb(owner_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"help:delete_confirm_chat:{owner_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:settings:{owner_id}"))
    return kb.as_markup()


def help_global_visibility_kb(owner_id: int, enabled: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="👁️ Видимость в рейтингах: Вкл" if enabled else "🙈 Видимость в рейтингах: Выкл",
            callback_data=f"help:global_vis_toggle:{owner_id}",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"help:settings:{owner_id}"))
    return kb.as_markup()
