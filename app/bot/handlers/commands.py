from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from app.bot.keyboards.help import help_root_kb
from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.stats import stats_root_kb
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.time_service import get_session_window, now_in_tz
from app.services.repo_service import (
    upsert_chat,
    upsert_user,
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
)
from app.services.q1_service import render_q1
from app.services.command_message_service import (
    get_command_message_id,
    get_any_command_message_id,
    set_command_message_id,
)

router = Router()

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


@router.message(Command("start"))
async def start_cmd(message: Message) -> None:
    if message.chat is None or message.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = message.chat.id
    user = message.from_user

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)

        window = get_session_window(chat.timezone)
        if window.is_blocked_window:
            await message.answer("Новая сессия начнётся в 00:05")
            return

        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)
        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")

        if q1_msg_id:
            # пробуем реплаем на актуальный вопрос
            try:
                await message.answer("Актуальный вопрос за сессию выше 👆", reply_to_message_id=q1_msg_id)
                return
            except TelegramBadRequest as e:
                # сообщение удалено/недоступно — форсим новый Q1
                if "message to be replied not found" not in str(e).lower():
                    raise

        # создать новый Q1 и сохранить новый message_id
        text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
        has_any_members = "Участники:" in text
        sent = await message.answer(text, reply_markup=q1_keyboard(has_any_members))
        set_session_message_id(db, sess.session_id, "Q1", sent.message_id)


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    if message.chat is None or message.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = message.chat.id
    user = message.from_user

    root_text = (
        "ℹ️ Помощь\n\n"
        "💩 Кнопки Q1:\n"
        "• +💩 / -💩 — отметить сколько раз сегодня\n"
        "• ⏳ — подписка/отписка на напоминалку в 22:00\n\n"
        "🧻 Q2/Q3:\n"
        "• можно выбрать только если сегодня 💩 > 0\n"
    )

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        window = get_session_window(chat.timezone)
        session_date = window.session_date
        existing_mid = get_any_command_message_id(db, chat_id, "help", session_date)

    if existing_mid:
        try:
            await message.answer("Меню помощи выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            if "message to be replied not found" not in str(e).lower():
                raise
            # help-сообщение удалили — создадим новое ниже

    sent = await message.answer(root_text, reply_markup=help_root_kb(user.id))

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        window = get_session_window(chat.timezone)
        set_command_message_id(db, chat_id, user.id, "help", window.session_date, sent.message_id)


@router.message(Command("stats"))
async def stats_cmd(message: Message) -> None:
    if message.chat is None or message.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = message.chat.id
    user = message.from_user

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        today = now_in_tz(chat.timezone).date()
        existing_mid = get_command_message_id(db, chat_id, user.id, "stats", today)

    if existing_mid:
        try:
            await message.answer("Твоя статистика выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            if "message to be replied not found" not in str(e).lower():
                raise
            # stats-сообщение удалили — создадим новое

    text = "📊 Статистика\n\nВыбери раздел:"
    sent = await message.answer(text, reply_markup=stats_root_kb())

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        today = now_in_tz(chat.timezone).date()
        set_command_message_id(db, chat_id, user.id, "stats", today, sent.message_id)
