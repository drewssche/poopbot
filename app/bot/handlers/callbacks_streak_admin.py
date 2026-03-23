from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.handlers.streak_admin_content import (
    streak_admin_group_picker_text,
    streak_admin_result_text,
    streak_admin_text,
)
from app.bot.keyboards.streak_admin import streak_admin_group_picker_kb, streak_admin_kb
from app.core.config import load_settings
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.q1_service import undo_restore_for_date
from app.services.scheduler_service import _refresh_current_q1_view
from app.services.streak_restore_service import (
    collect_streak_restore_incident_stats,
    detect_suspected_streak_incident_dates,
    list_active_group_chat_ids,
    send_streak_restore_battle_message,
    send_streak_restore_incident_message_to_chat,
    send_streak_restore_incident_messages,
    send_streak_restore_preview_message,
)
from app.services.time_service import get_session_window
from app.services.repo_service import upsert_chat

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


def _format_chat_title(raw: str, fallback_index: int) -> str:
    title = (raw or "").strip()
    if not title:
        title = f"Группа {fallback_index}"
    return title[:48]


async def _resolve_group_options(cb: CallbackQuery, chat_ids: list[int]) -> list[tuple[int, str]]:
    options: list[tuple[int, str]] = []
    for idx, cid in enumerate(chat_ids, start=1):
        try:
            chat = await cb.bot.get_chat(cid)
            title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or ""
        except Exception:
            title = ""
        options.append((cid, _format_chat_title(str(title), idx)))
    return options


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
            if parts[2] == "auto":
                with db_session(_session_factory) as db:
                    candidates = detect_suspected_streak_incident_dates(db, today=date.today())
                if candidates:
                    target_date = date.fromisoformat(str(candidates[0]["date"]))
                else:
                    target_date = date.today()
            else:
                target_date = date.fromisoformat(parts[2])
            with db_session(_session_factory) as db:
                candidates = detect_suspected_streak_incident_dates(db, today=date.today())
            await cb.message.edit_text(
                streak_admin_text(target_date, candidates=candidates),
                reply_markup=streak_admin_kb(target_date),
                parse_mode="Markdown",
            )
            await cb.answer()
            return
        if action == "panel":
            target_date = date.fromisoformat(parts[2])
            with db_session(_session_factory) as db:
                candidates = detect_suspected_streak_incident_dates(db, today=date.today())
            await cb.message.edit_text(
                streak_admin_text(target_date, candidates=candidates),
                reply_markup=streak_admin_kb(target_date),
                parse_mode="Markdown",
            )
            await cb.answer()
            return
        if action == "groupmenu":
            page = max(0, int(parts[2]))
            target_date = date.fromisoformat(parts[3])
            with db_session(_session_factory) as db:
                group_chat_ids = list_active_group_chat_ids(db)
            group_options = await _resolve_group_options(cb, group_chat_ids)
            await cb.message.edit_text(
                streak_admin_group_picker_text(target_date, page=page, total_groups=len(group_options)),
                reply_markup=streak_admin_group_picker_kb(target_date, group_options, page),
                parse_mode="Markdown",
            )
            await cb.answer()
            return

        target_date = date.fromisoformat(parts[-1])
        if action == "preview":
            await send_streak_restore_preview_message(cb.bot, owner_chat_id=cb.message.chat.id, target_date=target_date)
            await cb.answer("Превью отправлено в эту личку", show_alert=False)
            return
        if action == "battle":
            await send_streak_restore_battle_message(cb.bot, owner_chat_id=cb.message.chat.id, target_date=target_date)
            await cb.answer("Боевое сообщение отправлено в эту личку", show_alert=False)
            return
        if action == "undo":
            with db_session(_session_factory) as db:
                chat = upsert_chat(db, chat_id=cb.message.chat.id)
                changed, message = undo_restore_for_date(db, cb.message.chat.id, cb.from_user.id, target_date)
                db.commit()
                if changed:
                    current_session_date = get_session_window(chat.timezone).session_date
                    await _refresh_current_q1_view(cb.bot, db, cb.message.chat.id, current_session_date)
            await cb.answer(message, show_alert=not changed)
            return
        if action == "status":
            with db_session(_session_factory) as db:
                stats = collect_streak_restore_incident_stats(db, target_date=target_date)
            await cb.answer(
                f"groups sent={stats['groups_sent']} private sent={stats['private_sent']}",
                show_alert=True,
            )
            return
        if action == "groupsend":
            chat_id = int(parts[2])
            result = await send_streak_restore_incident_message_to_chat(
                cb.bot,
                _session_factory,
                chat_id=chat_id,
                target_date=target_date,
            )
            if result["failed"]:
                await cb.answer("Ошибка отправки", show_alert=True)
            elif result["duplicate"]:
                await cb.answer("В эту группу уже отправляли", show_alert=False)
            else:
                await cb.answer(f"Отправлено в {chat_id}", show_alert=False)
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
