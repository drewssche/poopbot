from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.keyboards.help import (
    help_delete_chat_confirm_kb,
    help_delete_confirm_kb,
    help_global_visibility_kb,
    help_notifications_kb,
    help_root_kb,
    help_settings_kb,
)
from app.bot.keyboards.q1 import q1_keyboard
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.help_service import (
    delete_user_everywhere,
    delete_user_from_chat,
    set_chat_global_visibility,
    set_chat_notifications_enabled,
    set_chat_post_time,
)
from app.services.q1_service import render_q1
from app.services.q2_q3_service import ensure_q2_q3_exist
from app.services.repo_service import get_or_create_session, get_session_message_id, upsert_chat
from app.services.time_service import get_session_window, now_in_tz

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


def _root_text(tz_name: str) -> str:
    return (
        "ℹ️ Помощь\n\n"
        "Как пользоваться ботом:\n"
        "• `+💩` / `-💩` — увеличить или уменьшить количество за текущую сессию.\n"
        "• `⏳ Напомнить в 22:00` — включить/выключить вечернее напоминание.\n"
        "• Уточняющие вопросы доступны, когда у тебя есть хотя бы одно `+💩` в текущей сессии.\n"
        "• В уточняющих вопросах выбор применяется к твоему последнему походу.\n\n"
        "Где что смотреть:\n"
        "• `/stats` — личная, чатовая, глобальная и межчатовая статистика.\n"
        "• `⚙️ Настройки` — уведомления, удаление данных, видимость чата в рейтингах.\n"
        "• `🤖 О боте` — кратко о проекте и ссылка на репозиторий.\n\n"
        "Как работает сессия:\n"
        f"• Таймзона этого чата: `{tz_name}`.\n"
        "• Активная сессия: `00:05–23:55` по локальному времени чата.\n"
        "• Техническое окно: `23:55–00:05` — сессия закрывается/открывается, кнопки могут быть недоступны.\n"
        "• Напоминание в 22:00 и автопост вопросов работают в таймзоне чата.\n"
    )


def _settings_text(is_private_chat: bool) -> str:
    base = (
        "⚙️ Настройки\n\n"
        "Что можно настроить:\n"
        "• `🗑️ Удалить меня` — полное удаление твоего профиля и статистики из базы во всех чатах.\n"
        "  После этого вернуться можно через `+💩` или включение напоминания, но уже с новой статистикой.\n"
    )
    if not is_private_chat:
        base += (
            "• `🧹 Удалить меня из этого чата` — удаляет только участие в текущем чате.\n"
            "  Данные в других чатах и личке остаются.\n"
            "• `👁️ Видимость чата в рейтингах` — скрывает/показывает этот чат в межчатовых топах.\n"
        )
    base += (
        "• `🔔 Уведомления` — включить/выключить автопосты и напоминания, плюс выбрать время публикации.\n"
        "  Если выключить — бот не отправляет плановые сообщения в этот чат, но команды остаются рабочими.\n"
        "• `⬅️ Назад` — вернуться в главное меню помощи.\n"
    )
    return base


def _notifications_text(enabled: bool, post_time_text: str) -> str:
    state = "включены" if enabled else "выключены"
    time_line = f"Текущее время публикации: {post_time_text}." if enabled else "Текущее время публикации: выключены."
    return (
        "🔔 Уведомления\n\n"
        f"Текущий статус: {state}.\n"
        f"{time_line}\n\n"
        "Что включает этот раздел:\n"
        "• Автопост ежедневного вопроса.\n"
        "• Напоминание в 22:00 и позднее напоминание.\n"
        "• Плановые итоговые сообщения по расписанию.\n\n"
        "Команды `/start`, `/help`, `/stats` работают независимо от этого переключателя."
    )


def _global_visibility_text(enabled: bool) -> str:
    state = "включена" if enabled else "выключена"
    return (
        "👁️ Видимость чата в рейтингах\n\n"
        f"Текущий статус: {state}.\n\n"
        "На что влияет:\n"
        "• Раздел «Среди чатов» в /stats: этот чат будет скрыт.\n"
        "• Межчатовые рейтинги (топы, рекорд дня, «самый жидкий/сухой чат»): чат исключается из расчета.\n\n"
        "На что не влияет:\n"
        "• Локальная статистика этого чата (Моя / В этом чате).\n"
        "• Глобальная статистика пользователей внутри чата.\n"
        "• Ежедневные вопросы и напоминания.\n"
        "• Личный и чатовый рекапы.\n\n"
        "Итог: переключатель скрывает чат только из межчатовой витрины, "
        "но не отключает работу бота в самом чате."
    )


ABOUT_TEXT = (
    "🤖 О боте\n\n"
    "Бот ведет ежедневный трекер привычки в чате: задает вопросы, напоминает и собирает статистику.\n\n"
    "Что умеет:\n"
    "• ежедневная сессия с кнопками\n"
    "• уточняющие ответы по последнему походу\n"
    "• личная/чатовая/глобальная статистика\n"
    "• годовые рекапы карточками\n\n"
    "Проект на GitHub:\n"
    "https://github.com/drewssche/poopbot"
)


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
    is_private_chat = cb.message.chat.type == "private"
    owner_id = actor_id

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id)

        try:
            if data.startswith("help:settings:"):
                await cb.message.edit_text(
                    _settings_text(is_private_chat),
                    reply_markup=help_settings_kb(owner_id, is_private_chat=is_private_chat),
                )
                await cb.answer()

            elif data.startswith("help:about:"):
                await cb.message.edit_text(ABOUT_TEXT, reply_markup=help_root_kb(owner_id))
                await cb.answer()

            elif data.startswith("help:notifications:") or data.startswith("help:set_time:"):
                await cb.message.edit_text(
                    _notifications_text(bool(chat.notifications_enabled), chat.post_time.strftime("%H:%M")),
                    reply_markup=help_notifications_kb(
                        owner_id,
                        current_hour=chat.post_time.hour,
                        notifications_enabled=bool(chat.notifications_enabled),
                    ),
                )
                await cb.answer()

            elif data.startswith("help:notifications_on:"):
                set_chat_notifications_enabled(db, chat_id, True)
                db.flush()
                chat = upsert_chat(db, chat_id)
                await cb.message.edit_text(
                    _notifications_text(bool(chat.notifications_enabled), chat.post_time.strftime("%H:%M")),
                    reply_markup=help_notifications_kb(
                        owner_id,
                        current_hour=chat.post_time.hour,
                        notifications_enabled=bool(chat.notifications_enabled),
                    ),
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:notifications_off:"):
                set_chat_notifications_enabled(db, chat_id, False)
                db.flush()
                chat = upsert_chat(db, chat_id)
                await cb.message.edit_text(
                    _notifications_text(bool(chat.notifications_enabled), chat.post_time.strftime("%H:%M")),
                    reply_markup=help_notifications_kb(
                        owner_id,
                        current_hour=chat.post_time.hour,
                        notifications_enabled=bool(chat.notifications_enabled),
                    ),
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:global_vis:"):
                if is_private_chat:
                    await cb.answer("В личке этот пункт недоступен", show_alert=False)
                    return
                await cb.message.edit_text(
                    _global_visibility_text(bool(chat.show_in_global)),
                    reply_markup=help_global_visibility_kb(owner_id, bool(chat.show_in_global)),
                )
                await cb.answer()

            elif data.startswith("help:global_vis_on:"):
                if is_private_chat:
                    await cb.answer("В личке этот пункт недоступен", show_alert=False)
                    return
                set_chat_global_visibility(db, chat_id, True)
                db.flush()
                chat = upsert_chat(db, chat_id)
                await cb.message.edit_text(
                    _global_visibility_text(bool(chat.show_in_global)),
                    reply_markup=help_global_visibility_kb(owner_id, bool(chat.show_in_global)),
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:global_vis_off:"):
                if is_private_chat:
                    await cb.answer("В личке этот пункт недоступен", show_alert=False)
                    return
                set_chat_global_visibility(db, chat_id, False)
                db.flush()
                chat = upsert_chat(db, chat_id)
                await cb.message.edit_text(
                    _global_visibility_text(bool(chat.show_in_global)),
                    reply_markup=help_global_visibility_kb(owner_id, bool(chat.show_in_global)),
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:time:"):
                hour = int(data.split(":")[2])
                set_chat_post_time(db, chat_id, hour)
                db.flush()
                chat = upsert_chat(db, chat_id)
                await cb.answer("Готово", show_alert=False)
                await cb.message.edit_text(
                    _notifications_text(bool(chat.notifications_enabled), chat.post_time.strftime("%H:%M")),
                    reply_markup=help_notifications_kb(
                        owner_id,
                        current_hour=chat.post_time.hour,
                        notifications_enabled=bool(chat.notifications_enabled),
                    ),
                )

            elif data.startswith("help:delete_me:"):
                mention = f"@{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name
                await cb.message.edit_text(
                    f"⚠️ {mention}, удалить тебя из базы полностью?\n\n"
                    "Что это значит:\n"
                    "• Удаление из всех чатов, где ты участвовал(а).\n"
                    "• Сброс твоей статистики и стриков.\n"
                    "• Вернуться можно в любой момент: нажми +💩 или включи напоминание.\n"
                    "• Статистика начнется заново.",
                    reply_markup=help_delete_confirm_kb(owner_id),
                )
                await cb.answer()

            elif data.startswith("help:delete_me_chat:"):
                if is_private_chat:
                    await cb.answer("В личке этот пункт недоступен", show_alert=False)
                    return
                mention = f"@{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name
                await cb.message.edit_text(
                    f"⚠️ {mention}, удалить тебя только из этого чата?\n\n"
                    "Что это значит:\n"
                    "• Удалишься только из текущего чата.\n"
                    "• Данные в других чатах и личке останутся.\n"
                    "• В этом чате можно вернуться позже и начать заново.",
                    reply_markup=help_delete_chat_confirm_kb(owner_id),
                )
                await cb.answer()

            elif data.startswith("help:delete_confirm_db:") or data.startswith("help:delete_confirm_chat:"):
                expected_owner = _parse_owner(data)
                if actor_id != expected_owner:
                    await cb.answer("Это не твое подтверждение", show_alert=False)
                    return

                is_db_delete = data.startswith("help:delete_confirm_db:")
                if is_db_delete:
                    delete_user_everywhere(db, chat_id, actor_id)
                else:
                    delete_user_from_chat(db, chat_id, actor_id)

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
                        try:
                            await ensure_q2_q3_exist(cb.bot, db, chat_id, sess.session_id)
                        except Exception:
                            logger.exception("Failed to refresh Q2/Q3 after delete action")

                await cb.answer("Удалил", show_alert=False)
                done_text = (
                    "✅ Готово. Ты удален из базы."
                    if is_db_delete
                    else "✅ Готово. Ты удален из этого чата."
                )
                await cb.message.edit_text(done_text, reply_markup=help_root_kb(owner_id))

            elif data.startswith("help:back:"):
                await cb.message.edit_text(_root_text(chat.timezone), reply_markup=help_root_kb(owner_id))
                await cb.answer()

            else:
                await cb.answer()

        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.exception("Help edit failed: %s", e)
            await cb.answer("Ошибка (см. логи)", show_alert=False)
