from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.handlers.streak_admin_content import streak_admin_group_picker_text, streak_admin_result_text, streak_admin_text
from app.bot.keyboards.streak_admin import streak_admin_group_picker_kb, streak_admin_kb, streak_period_picker_kb
from app.core.config import load_settings
from app.db.runtime import get_session_factory
from app.db.session import db_session
from app.services.app_setting_service import RESTORE_CLAIM_ENABLED_KEY, get_bool_setting, set_bool_setting
from app.services.restore_period_service import choose_restore_period, ensure_restore_period, lock_restore_chat, undo_period
from app.services.scheduler_service import _refresh_current_q1_view
from app.services.scheduler_telegram import safe_edit_message_text
from app.services.streak_restore_service import (
    collect_streak_restore_incident_stats, list_active_group_chat_ids, refresh_streak_restore_messages,
    send_streak_restore_battle_message, send_streak_restore_incident_message_to_chat,
    send_streak_restore_incident_messages, send_streak_restore_preview_message,
)
from app.services.time_service import now_in_tz
from app.services.repo_service import upsert_chat

logger = logging.getLogger(__name__)
router = Router()
_session_factory = None


def init_db(database_url: str) -> None:
    global _session_factory
    _session_factory = get_session_factory(database_url)


def _is_owner(settings, user_id: int) -> bool:
    return settings.bot_owner_id is not None and int(settings.bot_owner_id) == int(user_id)


async def _resolve_group_options(cb: CallbackQuery, chat_ids: list[int]) -> list[tuple[int, str]]:
    options = []
    for idx, cid in enumerate(chat_ids, start=1):
        try:
            chat = await cb.bot.get_chat(cid)
            title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or ""
        except Exception:
            title = ""
        options.append((cid, (str(title).strip() or f"Группа {idx}")[:48]))
    return options


async def _edit_panel(cb, text, kb):
    await safe_edit_message_text(cb.bot, chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                                 text=text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("streakadmin:"))
async def streak_admin_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return
    settings = load_settings()
    if not _is_owner(settings, cb.from_user.id) or cb.message.chat.type != "private":
        await cb.answer("Недостаточно прав", show_alert=False)
        return
    init_db(settings.database_url)
    parts = cb.data.split(":")
    action = parts[1]
    try:
        with db_session(_session_factory) as db:
            chat = upsert_chat(db, chat_id=cb.message.chat.id)
            today = now_in_tz(chat.timezone).date()
            period = ensure_restore_period(db, today=today)
            enabled = get_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, default=False)
            if action == "toggle":
                enabled = not enabled
                set_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, enabled)
            elif action == "period":
                period = choose_restore_period(db, today=today, days=int(parts[2]))

        if action in {"toggle", "panel", "period"}:
            await _edit_panel(cb, streak_admin_text(today, restore_enabled=enabled, period=period),
                              streak_admin_kb(today, restore_enabled=enabled))
            await cb.answer(f"Период: {period.label}" if action == "period" else "Готово")
            if action == "period":
                failed = await refresh_streak_restore_messages(cb.bot, _session_factory)
                if failed:
                    await _edit_panel(cb, streak_admin_text(today, restore_enabled=enabled, period=period)
                        + f"\n\nНе удалось обновить даты в {failed} сообщениях. Повтори выбор периода для повторной попытки.",
                        streak_admin_kb(today, restore_enabled=enabled))
            return
        if action == "days":
            days = max(1, min(30, int(parts[2])))
            start, end = today - timedelta(days=days), today - timedelta(days=1)
            await _edit_panel(cb, f"📅 Выбери количество прошедших дней: {days}\n\n{start:%d.%m.%Y}–{end:%d.%m.%Y}\nСегодня не включается.", streak_period_picker_kb(days))
            await cb.answer()
            return
        if action == "groupmenu":
            page = max(0, int(parts[2]))
            with db_session(_session_factory) as db:
                ids = list_active_group_chat_ids(db)
            await cb.answer()
            options = await _resolve_group_options(cb, ids)
            await _edit_panel(cb, streak_admin_group_picker_text(today, page=page, total_groups=len(options), period=period),
                              streak_admin_group_picker_kb(today, options, page))
            return
        if action in {"preview", "battle"}:
            await cb.answer("Отправляю")
            send = send_streak_restore_preview_message if action == "preview" else send_streak_restore_battle_message
            await send(cb.bot, _session_factory, owner_chat_id=cb.message.chat.id, target_date=today)
            return
        if action == "undo":
            with db_session(_session_factory) as db:
                lock_restore_chat(db, cb.message.chat.id)
                changed, message = undo_period(db, chat_id=cb.message.chat.id, user_id=cb.from_user.id, period=period, today=today)
                db.commit()
                if changed:
                    await _refresh_current_q1_view(cb.bot, db, cb.message.chat.id, today)
            await cb.answer(message, show_alert=True)
            return
        if action == "status":
            with db_session(_session_factory) as db:
                stats = collect_streak_restore_incident_stats(db, target_date=today)
            await cb.answer(f"Отправлено сегодня: группы {stats['groups_sent']}, лички {stats['private_sent']}\nПериод: {period.label}", show_alert=True)
            return
        if action == "groupsend":
            await cb.answer("Отправляю")
            result = await send_streak_restore_incident_message_to_chat(cb.bot, _session_factory, chat_id=int(parts[2]), target_date=today, force=True)
            await _edit_panel(cb, streak_admin_result_text("выбранная группа", today, period=period, sent=result['sent'], skipped=result['skipped'], failed=result['failed']),
                              streak_admin_kb(today, restore_enabled=enabled))
            return
        if action in {"send", "resend"}:
            scope = parts[2]
            await cb.answer("Рассылка началась")
            result = await send_streak_restore_incident_messages(cb.bot, _session_factory, target_date=today, scope=scope,
                        chat_throttle_sec=settings.scheduler_chat_throttle_sec, force=action == "resend")
            await _edit_panel(cb, streak_admin_result_text("все группы" if scope == "groups" else "все лички", today, period=period, **result),
                              streak_admin_kb(today, restore_enabled=enabled))
    except Exception:
        logger.exception("Streak admin action failed action=%s", action)
        try:
            await cb.answer("Ошибка, попробуй ещё раз", show_alert=True)
        except TelegramBadRequest:
            pass
