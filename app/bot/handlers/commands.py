from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.keyboards.help import help_root_kb
from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.recap import recap_announce_kb
from app.bot.keyboards.stats import stats_root_kb
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.command_message_service import (
    get_any_command_message_id,
    get_command_message_id,
    set_command_message_id,
)
from app.services.q1_service import render_q1
from app.services.q2_q3_service import ensure_q2_q3_exist
from app.services.recap_service import is_recap_available
from app.services.repo_service import (
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
    upsert_chat,
    upsert_user,
)
from app.services.time_service import get_session_window, now_in_tz

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
            try:
                await message.answer("Актуальный вопрос за сессию выше 👆", reply_to_message_id=q1_msg_id)
                await ensure_q2_q3_exist(message.bot, db, chat_id, sess.session_id)
                return
            except TelegramBadRequest as e:
                if "message to be replied not found" not in str(e).lower():
                    raise

        text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
        has_any_members = "Участники:" in text
        show_remind = now_in_tz(chat.timezone).time().hour < 22

        if window.session_date.month == 12 and window.session_date.day == 30:
            sent_recap_mid = get_command_message_id(db, chat_id, 0, "recap_announce", window.session_date)
            if sent_recap_mid is None:
                recap_text = (
                    "🎉 Доступен рекап года.\n"
                    "Запустить можно этой кнопкой или через `/stats`."
                    if chat_id > 0
                    else "🎉 Доступен рекап года. Забирай итоги!"
                )
                recap_sent = await message.answer(
                    recap_text,
                    reply_markup=recap_announce_kb(),
                )
                set_command_message_id(db, chat_id, 0, "recap_announce", window.session_date, recap_sent.message_id)

        sent = await message.answer(text, reply_markup=q1_keyboard(has_any_members, show_remind=show_remind))
        set_session_message_id(db, sess.session_id, "Q1", sent.message_id)
        await ensure_q2_q3_exist(message.bot, db, chat_id, sess.session_id)


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
        "💩 Основной вопрос дня:\n"
        "• `+💩` / `-💩` — отметить, сколько раз сегодня сходили\n"
        "• `⏳` — подписка/отписка на напоминалку в 22:00\n\n"
        "🧻 Уточняющие вопросы:\n"
        "• доступны, если сегодня уже есть хотя бы одно нажатие `+💩`\n\n"
    )

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        window = get_session_window(chat.timezone)
        session_date = window.session_date
        existing_mid = get_any_command_message_id(db, chat_id, "help", session_date)
        is_private_chat = message.chat.type == "private"

    if existing_mid and not is_private_chat:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_mid,
                text=root_text,
                reply_markup=help_root_kb(user.id),
            )
            await message.answer("Меню помощи выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                await message.answer("Меню помощи выше 👆", reply_to_message_id=existing_mid)
                return
            if all(
                x not in err
                for x in (
                    "message to edit not found",
                    "message to be replied not found",
                    "replied message not found",
                    "message_id_invalid",
                )
            ):
                raise

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
        is_private_chat = message.chat.type == "private"
        show_recap = is_recap_available(today, user.id, settings.bot_owner_id)
        if settings.bot_owner_id is not None and user.id == settings.bot_owner_id:
            show_recap = is_private_chat

    text = "📊 Статистика\n\nВыбери раздел:"
    if show_recap:
        text += "\n\n🎉 Доступен Рекап года"

    if existing_mid and not is_private_chat:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_mid,
                text=text,
                reply_markup=stats_root_kb(show_recap=show_recap),
            )
            await message.answer("Твоя статистика выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                await message.answer("Твоя статистика выше 👆", reply_to_message_id=existing_mid)
                return
            if all(
                x not in err
                for x in (
                    "message to edit not found",
                    "message to be replied not found",
                    "replied message not found",
                    "message_id_invalid",
                )
            ):
                raise

    sent = await message.answer(text, reply_markup=stats_root_kb(show_recap=show_recap))

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        today = now_in_tz(chat.timezone).date()
        set_command_message_id(db, chat_id, user.id, "stats", today, sent.message_id)
