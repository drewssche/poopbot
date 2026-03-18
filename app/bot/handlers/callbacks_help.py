from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.handlers.help_actions import (
    refresh_q1_after_settings_change,
    render_global_visibility_panel,
    render_notifications_panel,
)
from app.bot.handlers.help_content import (
    ABOUT_TEXT,
    root_text,
    settings_text,
)
from app.bot.keyboards.help import (
    help_delete_chat_confirm_kb,
    help_delete_confirm_kb,
    help_root_kb,
    help_settings_kb,
)
from app.db.engine import make_engine, make_session_factory
from app.db.models import User
from app.db.session import db_session
from app.services.help_service import (
    delete_user_everywhere,
    delete_user_from_chat,
    set_chat_global_visibility,
    set_chat_late_reminder_enabled,
    set_chat_notifications_enabled,
    set_chat_post_time,
    set_chat_q2_q3_enabled,
    set_user_disable_mentions,
)
from app.services.repo_service import upsert_chat

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None


def _is_stale_callback_error(e: TelegramBadRequest) -> bool:
    msg = str(e).lower()
    return "query is too old" in msg or "query id is invalid" in msg


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _parse_owner(data: str) -> int:
    return int(data.split(":")[-1])


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
        actor_user = db.get(User, actor_id)
        actor_disable_mentions = bool(actor_user.disable_mentions) if actor_user is not None else False

        try:
            if data.startswith("help:settings:"):
                await cb.message.edit_text(
                    settings_text(is_private_chat),
                    reply_markup=help_settings_kb(owner_id, is_private_chat=is_private_chat),
                )
                await cb.answer()

            elif data.startswith("help:about:"):
                await cb.message.edit_text(ABOUT_TEXT, reply_markup=help_root_kb(owner_id))
                await cb.answer()

            elif data.startswith("help:notifications:") or data.startswith("help:set_time:"):
                await render_notifications_panel(
                    cb,
                    owner_id=owner_id,
                    chat=chat,
                    actor_disable_mentions=actor_disable_mentions,
                )
                await cb.answer()

            elif (
                data.startswith("help:notifications_toggle:")
                or data.startswith("help:notifications_on:")
                or data.startswith("help:notifications_off:")
            ):
                set_chat_notifications_enabled(db, chat_id, not bool(chat.notifications_enabled))
                db.flush()
                chat = upsert_chat(db, chat_id)
                await render_notifications_panel(
                    cb,
                    owner_id=owner_id,
                    chat=chat,
                    actor_disable_mentions=actor_disable_mentions,
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:late_reminder_toggle:"):
                set_chat_late_reminder_enabled(db, chat_id, not bool(chat.late_reminder_enabled))
                db.flush()
                chat = upsert_chat(db, chat_id)
                await render_notifications_panel(
                    cb,
                    owner_id=owner_id,
                    chat=chat,
                    actor_disable_mentions=actor_disable_mentions,
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:q2_q3_toggle:"):
                set_chat_q2_q3_enabled(db, chat_id, not bool(chat.q2_q3_enabled))
                db.flush()
                chat = upsert_chat(db, chat_id)
                await refresh_q1_after_settings_change(
                    cb,
                    db=db,
                    chat=chat,
                    chat_id=chat_id,
                    actor_id=actor_id,
                    is_private_chat=is_private_chat,
                )
                await render_notifications_panel(
                    cb,
                    owner_id=owner_id,
                    chat=chat,
                    actor_disable_mentions=actor_disable_mentions,
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:mentions_toggle:"):
                set_user_disable_mentions(db, actor_id, not actor_disable_mentions)
                db.flush()
                actor_user = db.get(User, actor_id)
                actor_disable_mentions = bool(actor_user.disable_mentions) if actor_user is not None else False
                await render_notifications_panel(
                    cb,
                    owner_id=owner_id,
                    chat=chat,
                    actor_disable_mentions=actor_disable_mentions,
                )
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:global_vis:"):
                if is_private_chat:
                    await cb.answer("В личке этот пункт недоступен", show_alert=False)
                    return
                await render_global_visibility_panel(cb, owner_id=owner_id, chat=chat)
                await cb.answer()

            elif (
                data.startswith("help:global_vis_toggle:")
                or data.startswith("help:global_vis_on:")
                or data.startswith("help:global_vis_off:")
            ):
                if is_private_chat:
                    await cb.answer("В личке этот пункт недоступен", show_alert=False)
                    return
                set_chat_global_visibility(db, chat_id, not bool(chat.show_in_global))
                db.flush()
                chat = upsert_chat(db, chat_id)
                await render_global_visibility_panel(cb, owner_id=owner_id, chat=chat)
                await cb.answer("Готово", show_alert=False)

            elif data.startswith("help:time:"):
                hour = int(data.split(":")[2])
                set_chat_post_time(db, chat_id, hour)
                db.flush()
                chat = upsert_chat(db, chat_id)
                await cb.answer("Готово", show_alert=False)
                await render_notifications_panel(
                    cb,
                    owner_id=owner_id,
                    chat=chat,
                    actor_disable_mentions=actor_disable_mentions,
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

                await refresh_q1_after_settings_change(
                    cb,
                    db=db,
                    chat=chat,
                    chat_id=chat_id,
                    actor_id=actor_id,
                    is_private_chat=is_private_chat,
                )

                await cb.answer("Удалил", show_alert=False)
                done_text = (
                    "✅ Готово. Ты удален из базы."
                    if is_db_delete
                    else "✅ Готово. Ты удален из этого чата."
                )
                await cb.message.edit_text(done_text, reply_markup=help_root_kb(owner_id))

            elif data.startswith("help:back:"):
                await cb.message.edit_text(root_text(chat.timezone), reply_markup=help_root_kb(owner_id))
                await cb.answer()

            else:
                await cb.answer()

        except TelegramBadRequest as e:
            if _is_stale_callback_error(e):
                return
            if "message is not modified" in str(e).lower():
                return
            logger.exception("Help edit failed: %s", e)
            try:
                await cb.answer("Ошибка (см. логи)", show_alert=False)
            except TelegramBadRequest as answer_err:
                if _is_stale_callback_error(answer_err):
                    return
                raise
