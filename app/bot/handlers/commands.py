from __future__ import annotations

from datetime import date, timedelta

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.keyboards.help import help_root_kb
from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.recap import recap_announce_kb
from app.bot.keyboards.streak_admin import streak_admin_kb
from app.bot.keyboards.stats import stats_root_kb
from app.bot.handlers.streak_admin_content import streak_admin_text
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.command_message_service import (
    get_any_command_message_id,
    get_command_message_id,
    set_command_message_id,
)
from app.services.q1_service import render_q1, render_q1_private, should_show_restore_streak_button
from app.services.q2_q3_service import ensure_q2_q3_exist, should_show_q2_q3_button
from app.services.recap_service import is_recap_available
from app.services.repo_service import (
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
    upsert_chat,
    upsert_user,
)
from app.services.app_setting_service import RESTORE_CLAIM_ENABLED_KEY, get_bool_setting
from app.services.scheduler_telegram import safe_edit_message_text
from app.services.time_service import get_session_window, now_in_tz

router = Router()

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _is_owner(settings, user_id: int) -> bool:
    return settings.bot_owner_id is not None and int(settings.bot_owner_id) == int(user_id)


def _help_root_text(tz_name: str) -> str:
    return (
        "ℹ️ Помощь\n\n"
        "Как пользоваться ботом:\n"
        "• `+💩` / `-💩` — увеличить или уменьшить количество за текущую сессию.\n"
        "• Уточняющие вопросы доступны, если у тебя уже есть хотя бы одно `+💩` за сессию.\n"
        "• В уточняющих вопросах выбор применяется к твоему последнему походу.\n\n"
        "Куда нажимать дальше:\n"
        "• `⚙️ Настройки` — уведомления, удаление данных, видимость чата в рейтингах.\n"
        "• `🤖 О боте` — что умеет бот и ссылка на репозиторий.\n"
        "• `/stats` — подробная статистика (моя, чатовая, глобальная, между чатами).\n\n"
        "Как работает сессия:\n"
        f"• Таймзона этого чата: `{tz_name}`.\n"
        "• Активная сессия: `00:05–23:55` по локальному времени чата.\n"
        "• Техническое окно: `23:55–00:05` — сессия закрывается/открывается, кнопки могут быть недоступны.\n"
        "• Автопост вопросов и автонапоминания работают в таймзоне чата.\n"
    )


def _stats_root_text(show_recap: bool, is_owner_private: bool, is_private_chat: bool) -> str:
    text = (
        "📊 Статистика\n\n"
        "Разделы:\n"
        "• 🙋 Моя — твоя статистика по всем чатам (за всё время).\n"
    )
    if is_private_chat:
        text += "• 💬 В этой личке — статистика отметок, сделанных именно в этой личке.\n"
    else:
        text += "• 👥 В этом чате — статистика отметок, сделанных именно в этом чате.\n"
    text += (
        "• 🏟️ Среди чатов — межчатовые топы (только чаты, у которых включена видимость).\n"
        "• 🌍 Глобальная — обезличенные топы + твое место в глобальном рейтинге.\n"
    )
    if show_recap:
        if is_owner_private:
            text += "\n🎉 Рекап года доступен (для владельца всегда в личке)."
        else:
            text += "\n🎉 Рекап года доступен (обычно с 30 декабря по 3 января включительно)."
    return text


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
            await message.answer("Новая сессия начнется в 00:05")
            return

        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)
        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")

        if q1_msg_id:
            try:
                is_private_chat = message.chat.type == "private"
                text = (
                    render_q1_private(
                        db,
                        chat_id=chat_id,
                        session_id=sess.session_id,
                        user_id=user.id,
                        session_date=window.session_date,
                    )
                    if is_private_chat
                    else render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
                )
                has_any_members = True if is_private_chat else ("Участники:" in text)
                await safe_edit_message_text(
                    message.bot,
                    chat_id=chat_id,
                    message_id=q1_msg_id,
                    text=text,
                    reply_markup=q1_keyboard(
                        has_any_members,
                        show_restore_streak_button=should_show_restore_streak_button(
                            db,
                            chat_id=chat_id,
                            session_date=window.session_date,
                            viewer_user_id=user.id if is_private_chat else None,
                            is_private_chat=is_private_chat,
                        ),
                        show_q2_q3_button=should_show_q2_q3_button(
                            db,
                            chat_q2_q3_enabled=bool(chat.q2_q3_enabled),
                            session_id=sess.session_id,
                            is_private_chat=is_private_chat,
                        ),
                    ),
                )
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "message is not modified" not in err and "message to edit not found" not in err:
                    raise
            try:
                await message.answer("Актуальный вопрос за сессию выше 👆", reply_to_message_id=q1_msg_id)
                if bool(chat.q2_q3_enabled) and chat_id < 0:
                    await ensure_q2_q3_exist(message.bot, db, chat_id, sess.session_id)
                return
            except TelegramBadRequest as e:
                if "message to be replied not found" not in str(e).lower():
                    raise

        is_private_chat = message.chat.type == "private"
        text = (
            render_q1_private(db, chat_id=chat_id, session_id=sess.session_id, user_id=user.id, session_date=window.session_date)
            if is_private_chat
            else render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
        )
        has_any_members = True if is_private_chat else ("Участники:" in text)

        if window.session_date.month == 12 and window.session_date.day == 30:
            sent_recap_mid = get_command_message_id(db, chat_id, 0, "recap_announce", window.session_date)
            if sent_recap_mid is None:
                recap_text = (
                    "🎉 Доступен рекап года.\n"
                    "Запустить можно этой кнопкой или через `/stats`."
                    if chat_id > 0
                    else "🎉 Доступен рекап года. Забирай итоги!"
                )
                recap_sent = await message.answer(recap_text, reply_markup=recap_announce_kb())
                set_command_message_id(db, chat_id, 0, "recap_announce", window.session_date, recap_sent.message_id)

        sent = await message.answer(
            text,
            reply_markup=q1_keyboard(
                has_any_members,
                show_restore_streak_button=should_show_restore_streak_button(
                    db,
                    chat_id=chat_id,
                    session_date=window.session_date,
                    viewer_user_id=user.id if is_private_chat else None,
                    is_private_chat=is_private_chat,
                ),
                show_q2_q3_button=should_show_q2_q3_button(
                    db,
                    chat_q2_q3_enabled=bool(chat.q2_q3_enabled),
                    session_id=sess.session_id,
                    is_private_chat=is_private_chat,
                ),
            ),
        )
        set_session_message_id(db, sess.session_id, "Q1", sent.message_id)
        if bool(chat.q2_q3_enabled) and chat_id < 0:
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

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        window = get_session_window(chat.timezone)
        session_date = window.session_date
        existing_mid = get_any_command_message_id(db, chat_id, "help", session_date)
        is_private_chat = message.chat.type == "private"
        root_text = _help_root_text(chat.timezone)

    if existing_mid:
        try:
            await safe_edit_message_text(
                message.bot,
                chat_id=chat_id,
                message_id=existing_mid,
                text=root_text,
                reply_markup=help_root_kb(user.id),
            )
            if not is_private_chat:
                await message.answer("Меню помощи выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                if not is_private_chat:
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
        is_owner_private = settings.bot_owner_id is not None and user.id == settings.bot_owner_id and is_private_chat
        if settings.bot_owner_id is not None and user.id == settings.bot_owner_id:
            show_recap = False

    text = _stats_root_text(
        show_recap=show_recap,
        is_owner_private=is_owner_private,
        is_private_chat=is_private_chat,
    )

    if existing_mid:
        try:
            await safe_edit_message_text(
                message.bot,
                chat_id=chat_id,
                message_id=existing_mid,
                text=text,
                reply_markup=stats_root_kb(show_recap=show_recap, is_private_chat=is_private_chat),
            )
            if not is_private_chat:
                await message.answer("Твоя статистика выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                if not is_private_chat:
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

    sent = await message.answer(text, reply_markup=stats_root_kb(show_recap=show_recap, is_private_chat=is_private_chat))

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        today = now_in_tz(chat.timezone).date()
        set_command_message_id(db, chat_id, user.id, "stats", today, sent.message_id)


@router.message(Command("streak"))
async def streak_admin_cmd(message: Message) -> None:
    if message.chat is None or message.from_user is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    if not _is_owner(settings, message.from_user.id):
        return

    init_db(settings.database_url)
    chat_id = message.chat.id
    user = message.from_user

    if message.chat.type != "private":
        await message.answer("Открой эту команду в личке с ботом.")
        return

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
        today = now_in_tz(chat.timezone).date()
        target_date = today - timedelta(days=1)
        existing_mid = get_command_message_id(db, chat_id, user.id, "streak_admin", today)
        restore_enabled = get_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, default=False)

    text = streak_admin_text(target_date, restore_enabled=restore_enabled)
    kb = streak_admin_kb(target_date, restore_enabled=restore_enabled)

    if existing_mid:
        try:
            await safe_edit_message_text(
                message.bot,
                chat_id=chat_id,
                message_id=existing_mid,
                text=text,
                reply_markup=kb,
                parse_mode="Markdown",
            )
            await message.answer("Панель выше 👆", reply_to_message_id=existing_mid)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                await message.answer("Панель выше 👆", reply_to_message_id=existing_mid)
                return
            if all(x not in err for x in ("message to edit not found", "message_id_invalid", "message not found")):
                raise

    sent = await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        today = now_in_tz(chat.timezone).date()
        set_command_message_id(db, chat_id, user.id, "streak_admin", today, sent.message_id)
