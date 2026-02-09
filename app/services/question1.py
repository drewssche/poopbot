from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_question1() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="💩 Я", callback_data="q1:poop"),
        InlineKeyboardButton(text="❌ Не", callback_data="q1:no"),
    )
    kb.row(
        InlineKeyboardButton(
            text="⏳ Спроси меня позже (через 2 часа)", callback_data="q1:later")
    )
    return kb.as_markup()


def render_question1_empty(date_str: str) -> str:
    return (
        f"💩 Кто сегодня какал? ({date_str})\n\n"
        "Пока здесь никого нет в списке.\n"
        "Нажми любую кнопку ниже, чтобы участвовать — и я добавлю тебя на будущее."
    )
