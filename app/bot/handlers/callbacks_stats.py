from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.keyboards.stats import (
    PERIOD_ALL,
    PERIOD_TODAY,
    SCOPE_AMONG,
    SCOPE_CHAT,
    SCOPE_GLOBAL,
    SCOPE_MY,
    stats_among_kb,
    stats_global_kb,
    stats_period_kb,
    stats_root_kb,
)
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.repo_service import upsert_chat, upsert_user
from app.services.stats_service import (
    build_stats_text_chat,
    build_stats_text_global,
    build_stats_text_my,
    collect_among_chats_snapshot,
)
from app.services.time_service import now_in_tz

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
    return build_stats_text_global(db, user_id, today, PERIOD_ALL)


@router.callback_query(F.data.startswith("stats:"))
async def stats_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user
    data = cb.data or ""

    with db_session(_session_factory) as db:
        upsert_chat(db, chat_id)
        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        parts = data.split(":")

        # stats:open:{scope}
        if len(parts) == 3 and parts[1] == "open":
            scope = parts[2]
            if scope not in (SCOPE_MY, SCOPE_CHAT, SCOPE_AMONG, SCOPE_GLOBAL):
                await cb.answer()
                return

            if scope == SCOPE_AMONG:
                text = await _render_among_chats(cb, db)
                await _edit(cb, text, stats_among_kb())
                return

            if scope == SCOPE_GLOBAL:
                text = _render(db, chat_id, user.id, scope, PERIOD_ALL)
                await _edit(cb, text, stats_global_kb())
                return

            text = _render(db, chat_id, user.id, scope, PERIOD_TODAY)
            await _edit(cb, text, stats_period_kb(scope, PERIOD_TODAY))
            return

        # stats:period:{scope}:{period}
        if len(parts) == 4 and parts[1] == "period":
            scope = parts[2]
            period = parts[3]
            if scope not in (SCOPE_MY, SCOPE_CHAT):
                await cb.answer()
                return

            text = _render(db, chat_id, user.id, scope, period)
            await _edit(cb, text, stats_period_kb(scope, period))
            return

        # stats:global:me
        if len(parts) == 3 and parts[1] == "global" and parts[2] == "me":
            text = _render(db, chat_id, user.id, SCOPE_GLOBAL, PERIOD_ALL)
            await _edit(cb, text, stats_global_kb())
            return

        # stats:back:root
        if len(parts) == 3 and parts[1] == "back" and parts[2] == "root":
            await _edit(cb, "📊 Статистика\n\nВыбери раздел:", stats_root_kb())
            return

    await cb.answer()


async def _render_among_chats(cb: CallbackQuery, db) -> str:
    from app.db.models import Chat

    cur_chat = db.get(Chat, cb.message.chat.id)
    tz = cur_chat.timezone if cur_chat else "Europe/Minsk"
    today = now_in_tz(tz).date()
    snap = collect_among_chats_snapshot(db, today)

    ids = set()
    ids.update(chat_id for chat_id, _ in snap["top_total"])
    ids.update(chat_id for chat_id, _, _, _ in snap["top_avg"])
    ids.update(chat_id for chat_id, _ in snap["top_streak"])
    if snap["record_day"] is not None:
        ids.add(snap["record_day"][0])

    names: dict[int, str] = {}
    for cid in ids:
        try:
            chat_obj = await cb.bot.get_chat(cid)
            title = getattr(chat_obj, "title", None) or getattr(chat_obj, "full_name", None)
            names[cid] = (title or f"Чат {cid}").strip()
        except Exception:
            names[cid] = f"Чат {cid}"

    def chat_name(cid: int) -> str:
        return names.get(cid, f"Чат {cid}")

    lines = [
        "🏟️ Среди чатов",
        "Период: за всё время",
        "",
        "Топ-5 по общему количеству 💩:",
    ]

    if snap["top_total"]:
        for idx, (cid, total) in enumerate(snap["top_total"], start=1):
            lines.append(f"- {idx}) {chat_name(cid)} — 💩({total})")
    else:
        lines.append("- пока нет данных")

    lines.extend(["", "Топ-5 по среднему на участника:"])
    if snap["top_avg"]:
        for idx, (cid, avg, total, participants) in enumerate(snap["top_avg"], start=1):
            lines.append(f"- {idx}) {chat_name(cid)} — {avg:.2f} (💩({total}), участников: {participants})")
    else:
        lines.append("- пока нет данных")

    lines.extend(["", "Топ-5 по лучшему стрику чата:"])
    if snap["top_streak"]:
        for idx, (cid, days) in enumerate(snap["top_streak"], start=1):
            lines.append(f"- {idx}) {chat_name(cid)} — {days} дн.")
    else:
        lines.append("- пока нет данных")

    lines.extend(["", "Рекорд дня:"])
    if snap["record_day"] is not None:
        cid, d, poops = snap["record_day"]
        lines.append(f"- {chat_name(cid)} — {d.strftime('%d.%m.%y')} (💩({poops}))")
    else:
        lines.append("- пока нет данных")

    return "\n".join(lines)


async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            await cb.answer()
            return
        logger.exception("Stats edit failed: %s", e)
        await cb.answer("Ошибка (см. логи)", show_alert=False)
