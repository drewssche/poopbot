from __future__ import annotations

import logging
from datetime import time as dtime

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from app.bot.keyboards.help import help_root_kb, help_settings_kb, help_time_kb, help_delete_confirm_kb
from app.bot.keyboards.q1 import q1_keyboard
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.help_service import set_chat_post_time, delete_user_everywhere
from app.services.repo_service import upsert_chat, get_or_create_session, get_session_message_id
from app.services.time_service import get_session_window, now_in_tz
from app.services.q1_service import render_q1

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _parse_owner(data: str) -> int:
    return int(data.split(":")[-1])


def _root_text() -> str:
    return (
        "ℹ️ Помощь\n\n"
        "💩 Основной вопрос дня:\n"
        "• +💩 / -💩 — отметить, сколько раз сегодня сходили\n"
        "• ⏰ — подписка/отписка на напоминалку в 22:00\n\n"
        "🧻 Уточняющие вопросы:\n"
        "• можно выбрать только если сегодня уже отмечали 💩 хотя бы 1 раз\n\n"
    )


SETTINGS_TEXT = (
    "⚙️ Настройки\n\n"
    "🗑️ Удалить меня — полностью убирает тебя из базы и статистики.\n"
    "⏱️ Установить время — меняет время автопоста ежедневных вопросов для этого чата.\n"
    "⬅️ Назад — вернуться в меню помощи.\n"
)

ABOUT_TEXT = (
    "🤖 О боте\n\n"
    "Бот помогает вести ежедневный трекер привычки в чате: напоминает, задает вопросы и собирает статистику.\n\n"
    "Проект на GitHub:\n"
    "https://github.com/drewssche/poopbot"
)


def _time_text(current_time: dtime) -> str:
    return f"⏱️ Установить время вопросов для этого чата:\n\nТекущее: {current_time.strftime('%H:%M')}"


@router.callback_query(F.data.startswith("help:"))
async def help_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    data = cb.data
    chat_id = cb.message.chat.id
    actor_id = cb.from_user.id

    # owner всегда тот, кто нажал
    owner_id = actor_id

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id)

        try:
            if data.startswith("help:settings:"):
                await cb.message.edit_text(SETTINGS_TEXT, reply_markup=help_settings_kb(owner_id))
                await cb.answer()

            elif data.startswith("help:about:"):
                await cb.message.edit_text(ABOUT_TEXT, reply_markup=help_root_kb(owner_id))
                await cb.answer()

            elif data.startswith("help:set_time:"):
                await cb.message.edit_text(_time_text(chat.post_time), reply_markup=help_time_kb(owner_id, chat.post_time.hour))
                await cb.answer()

            elif data.startswith("help:time:"):
                hour = int(data.split(":")[2])
                set_chat_post_time(db, chat_id, hour)
                db.flush()
                chat = upsert_chat(db, chat_id)  # заново читаем
                await cb.answer("Готово", show_alert=False)
                await cb.message.edit_text(_time_text(chat.post_time), reply_markup=help_time_kb(owner_id, chat.post_time.hour))

            elif data.startswith("help:delete_me:"):
                owner_id = actor_id
                mention = f"@{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name
                await cb.message.edit_text(
                    f"⚠️ {mention}, уверен(а), что хочешь удалить себя из базы?",
                    reply_markup=help_delete_confirm_kb(owner_id),
                )
                await cb.answer()

            elif data.startswith("help:delete_confirm:"):
                expected_owner = _parse_owner(data)
                if actor_id != expected_owner:
                    await cb.answer("Р­С‚Рѕ РЅРµ С‚РІРѕС‘ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ", show_alert=False)
                    return

                delete_user_everywhere(db, chat_id, actor_id)

                # обновить актуальный Q1 (если есть)
                window = get_session_window(chat.timezone)
                if not window.is_blocked_window:
                    sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)
                    q1_id = get_session_message_id(db, sess.session_id, "Q1")
                    if q1_id and sess.status != "closed":
                        text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
                        has_any_members = "Участники:" in text
                        try:
                            await cb.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=q1_id,
                                text=text,
                                reply_markup=q1_keyboard(
                                    has_any_members,
                                    show_remind=get_session_window(chat.timezone).is_blocked_window is False
                                    and now_in_tz(chat.timezone).time().hour < 22,
                                ),
                            )
                        except TelegramBadRequest as e:
                            if "message is not modified" not in str(e).lower():
                                logger.exception("Failed to edit Q1 after delete_me: %s", e)

                await cb.answer("Удалил", show_alert=False)
                await cb.message.edit_text("✅ Готово. Ты удалён из базы.", reply_markup=help_root_kb(owner_id))

            elif data.startswith("help:back:"):
                await cb.message.edit_text(_root_text(), reply_markup=help_root_kb(owner_id))
                await cb.answer()

            else:
                await cb.answer()

        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.exception("Help edit failed: %s", e)
            await cb.answer("Ошибка (см. логи)", show_alert=False)
