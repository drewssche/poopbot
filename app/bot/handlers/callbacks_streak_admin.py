from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.handlers.streak_admin_content import streak_admin_result_text, streak_admin_text
from app.bot.keyboards.streak_admin import streak_admin_kb
from app.core.config import load_settings
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.streak_restore_service import (
    collect_streak_restore_incident_stats,
    send_streak_restore_incident_messages,
)

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


@router.callback_query(F.data.startswith("streakadmin:"))
async def streak_admin_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    settings = load_settings()
    if not _is_owner(settings, cb.from_user.id):
        await cb.answer("Недостаточно прав", show_alert=False)
        return

    init_db(settings.database_url)
    parts = cb.data.split(":")
    action = parts[1]

    try:
        if action == "date":
            target_date = date.fromisoformat(parts[2])
            await cb.message.edit_text(
                streak_admin_text(target_date),
                reply_markup=streak_admin_kb(target_date),
                parse_mode="Markdown",
            )
            await cb.answer()
            return

        target_date = date.fromisoformat(parts[-1])
        if action == "status":
            with db_session(_session_factory) as db:
                stats = collect_streak_restore_incident_stats(db, target_date=target_date)
            await cb.answer(
                f"groups sent={stats['groups_sent']} private sent={stats['private_sent']}",
                show_alert=True,
            )
            return

        if action == "send":
            scope = parts[2]
            result = await send_streak_restore_incident_messages(
                cb.bot,
                _session_factory,
                target_date=target_date,
                scope=scope,
                chat_throttle_sec=settings.scheduler_chat_throttle_sec,
            )
            scope_label = "все группы" if scope == "groups" else "все лички"
            await cb.message.edit_text(
                streak_admin_result_text(
                    scope_label,
                    target_date,
                    sent=result["sent"],
                    skipped=result["skipped"],
                    failed=result["failed"],
                ),
                reply_markup=streak_admin_kb(target_date),
                parse_mode="Markdown",
            )
            await cb.answer("Готово", show_alert=False)
            return
    except TelegramBadRequest:
        raise
    except Exception:
        await cb.answer("Ошибка, попробуй ещё раз", show_alert=False)
