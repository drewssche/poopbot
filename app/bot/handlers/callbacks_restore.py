from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.core.config import load_settings
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.q1_service import restore_recent_streak_window
from app.services.scheduler_service import _refresh_current_q1_view
from app.services.rate_limit_service import check_rate_limit
from app.services.repo_service import ensure_chat_member, upsert_chat, upsert_user
from app.services.time_service import get_session_window

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


@router.callback_query(F.data == "restore:claim")
async def restore_streak_claim(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    settings = load_settings()
    init_db(settings.database_url)

    try:
        with db_session(_session_factory) as db:
            chat = upsert_chat(db, chat_id=cb.message.chat.id)
            upsert_user(
                db,
                user_id=cb.from_user.id,
                username=cb.from_user.username,
                first_name=cb.from_user.first_name,
                last_name=cb.from_user.last_name,
            )
            ensure_chat_member(db, chat_id=chat.chat_id, user_id=cb.from_user.id)

            if not check_rate_limit(db, chat_id=chat.chat_id, user_id=cb.from_user.id, scope="RESTORE_STREAK", cooldown_seconds=4):
                await cb.answer("Не так быстро, здоровяк", show_alert=False)
                return

            current_session_date = get_session_window(chat.timezone).session_date
            changed, message = restore_recent_streak_window(
                db,
                chat_id=chat.chat_id,
                user_id=cb.from_user.id,
                current_session_date=current_session_date,
            )
            db.commit()
            if changed:
                await _refresh_current_q1_view(cb.bot, db, chat.chat_id, current_session_date)
            await cb.answer(message, show_alert=not changed)
    except TelegramBadRequest:
        raise
    except Exception:
        logger.exception("Unhandled exception in restore_streak_claim")
        try:
            await cb.answer("Ошибка, попробуй ещё раз", show_alert=False)
        except Exception:
            pass
