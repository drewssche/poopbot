from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.repo_service import upsert_chat, upsert_user
from app.services.time_service import now_in_tz
from app.bot.keyboards.stats import (
    stats_root_kb,
    stats_period_kb,
    SCOPE_MY, SCOPE_CHAT, SCOPE_GLOBAL,
    PERIOD_TODAY,
)
from app.services.stats_service import build_stats_text_my, build_stats_text_chat, build_stats_text_global

logger = logging.getLogger(__name__)
router = Router()

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _render(db, chat_id: int, user_id: int, scope: str, period: str) -> str:
    from app.db.models import Chat
    chat = db.get(Chat, chat_id)
    tz = chat.timezone if chat else "Europe/Minsk"
    today = now_in_tz(tz).date()

    if scope == SCOPE_MY:
        return build_stats_text_my(db, chat_id, user_id, today, period)
    if scope == SCOPE_CHAT:
        return build_stats_text_chat(db, chat_id, today, period)
    return build_stats_text_global(db, user_id, today, period)


@router.callback_query(F.data.startswith("stats:"))
async def stats_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings
    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user
    data = cb.data

    with db_session(_session_factory) as db:
        upsert_chat(db, chat_id)
        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        parts = data.split(":")

        # stats:open:{scope}
        if len(parts) == 3 and parts[1] == "open":
            scope = parts[2]
            if scope not in (SCOPE_MY, SCOPE_CHAT, SCOPE_GLOBAL):
                await cb.answer()
                return

            # при входе в категорию сразу показываем период-меню (по умолчанию today)
            text = _render(db, chat_id, user.id, scope, PERIOD_TODAY)
            await _edit(cb, text, stats_period_kb(scope, PERIOD_TODAY))
            return

        # stats:period:{scope}:{period}
        if len(parts) == 4 and parts[1] == "period":
            scope = parts[2]
            period = parts[3]
            if scope not in (SCOPE_MY, SCOPE_CHAT, SCOPE_GLOBAL):
                await cb.answer()
                return

            text = _render(db, chat_id, user.id, scope, period)
            await _edit(cb, text, stats_period_kb(scope, period))
            return

        # stats:back:root
        if len(parts) == 3 and parts[1] == "back" and parts[2] == "root":
            await _edit(cb, "📊 Статистика\n\nВыбери раздел:", stats_root_kb())
            return

    await cb.answer()


async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        logger.exception("Stats edit failed: %s", e)
        await cb.answer("Ошибка (см. логи)", show_alert=False)
