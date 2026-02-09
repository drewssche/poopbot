from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_question2() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="😇 Прекрасно", callback_data="q2:good"))
    kb.row(InlineKeyboardButton(text="😐 Сойдёт", callback_data="q2:ok"))
    kb.row(InlineKeyboardButton(text="😫 Ужасно", callback_data="q2:bad"))
    return kb.as_markup()
