from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from app.bot.keyboards.stats import (
    PERIOD_ALL,
    SCOPE_AMONG,
    SCOPE_CHAT,
    SCOPE_GLOBAL,
    SCOPE_MY,
    stats_among_kb,
    stats_global_kb,
    stats_local_kb,
    stats_root_kb,
)
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.recap_service import is_recap_available
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


def _stats_root_text(show_recap: bool, is_owner_private: bool, is_private_chat: bool) -> str:
    text = (
        "📊 Статистика\n\n"
        "Разделы:\n"
        "• 🙋 Моя — твоя личная статистика по всем чатам (за всё время).\n"
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


def _render(db, chat_id: int, user_id: int, scope: str) -> str:
    from app.db.models import Chat

    chat = db.get(Chat, chat_id)
    tz = chat.timezone if chat else "Europe/Minsk"
    today = now_in_tz(tz).date()

    if scope == SCOPE_MY:
        return build_stats_text_my(db, chat_id, user_id, today, PERIOD_ALL)
    if scope == SCOPE_CHAT:
        return build_stats_text_chat(db, chat_id, today, PERIOD_ALL, user_id=user_id)
    return build_stats_text_global(db, user_id, today, PERIOD_ALL)


@router.callback_query(F.data.startswith("stats:"))
async def stats_callbacks(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    from app.core.config import load_settings
    from app.db.models import Chat

    settings = load_settings()
    init_db(settings.database_url)

    chat_id = cb.message.chat.id
    user = cb.from_user
    data = cb.data or ""

    with db_session(_session_factory) as db:
        upsert_chat(db, chat_id)
        upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)

        parts = data.split(":")

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
                text = _render(db, chat_id, user.id, scope)
                await _edit(cb, text, stats_global_kb(is_private_chat=(cb.message.chat.type == "private")))
                return

            text = _render(db, chat_id, user.id, scope)
            await _edit(cb, text, stats_local_kb())
            return

        if len(parts) == 3 and parts[1] == "global" and parts[2] == "me":
            text = _render(db, chat_id, user.id, SCOPE_GLOBAL)
            await _edit(cb, text, stats_global_kb(is_private_chat=(cb.message.chat.type == "private")))
            return

        if len(parts) == 3 and parts[1] == "back" and parts[2] == "root":
            chat = db.get(Chat, chat_id)
            tz = chat.timezone if chat else "Europe/Minsk"
            today = now_in_tz(tz).date()
            show_recap = is_recap_available(today, user.id, settings.bot_owner_id)
            is_owner_private = settings.bot_owner_id is not None and user.id == settings.bot_owner_id and cb.message.chat.type == "private"
            if settings.bot_owner_id is not None and user.id == settings.bot_owner_id:
                show_recap = False
            text = _stats_root_text(
                show_recap=show_recap,
                is_owner_private=is_owner_private,
                is_private_chat=(cb.message.chat.type == "private"),
            )
            await _edit(
                cb,
                text,
                stats_root_kb(show_recap=show_recap, is_private_chat=(cb.message.chat.type == "private")),
            )
            return

    await cb.answer()


async def _render_among_chats(cb: CallbackQuery, db) -> str:
    from app.db.models import Chat
    from app.db.models import Session as DaySession

    cur_chat = db.get(Chat, cb.message.chat.id)
    tz = cur_chat.timezone if cur_chat else "Europe/Minsk"
    today = now_in_tz(tz).date()
    snap = collect_among_chats_snapshot(db, today)
    bot_start = db.scalar(select(func.min(DaySession.session_date)))
    period_text = (
        f"Период: за всё время ({bot_start.strftime('%d.%m.%y')}–{today.strftime('%d.%m.%y')})"
        if bot_start is not None
        else "Период: за всё время"
    )

    ids = set()
    ids.update(chat_id for chat_id, _ in snap["top_total"])
    ids.update(chat_id for chat_id, _, _, _ in snap["top_avg"])
    ids.update(chat_id for chat_id, _ in snap["top_streak"])
    for cid, _d, _poops in snap.get("record_days", []):
        ids.add(cid)
    if snap["record_day"] is not None:
        ids.add(snap["record_day"][0])
    if snap.get("most_liquid") is not None:
        ids.add(snap["most_liquid"][0])
    if snap.get("most_dry") is not None:
        ids.add(snap["most_dry"][0])

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
        period_text,
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
        lines.append("")
        lines.append("Примечание: чатовый стрик считается по дневной активности в сессиях чата (включая синхронизированные отметки).")
    else:
        lines.append("- пока нет данных")

    lines.extend(["", "Рекорд дня:"])
    record_days = snap.get("record_days", [])
    if record_days:
        for cid, d, poops in record_days:
            lines.append(f"- {chat_name(cid)} — {d.strftime('%d.%m.%y')} (💩({poops}))")
    elif snap["record_day"] is not None:
        cid, d, poops = snap["record_day"]
        lines.append(f"- {chat_name(cid)} — {d.strftime('%d.%m.%y')} (💩({poops}))")
    else:
        lines.append("- пока нет данных")

    lines.extend(["", "Бристоль-экстрим:"])
    most_liquid = snap.get("most_liquid")
    most_dry = snap.get("most_dry")
    min_samples = int(snap.get("min_bristol_samples", 10))
    if most_liquid is None:
        lines.append(f"- 🥤 Самый жидкий чат: недостаточно данных (нужно минимум {min_samples} оценок)")
    else:
        cid, share, _liquid_n, total_n = most_liquid
        pct = int(round(float(share) * 100))
        lines.append(f"- 🥤 Самый жидкий чат: {chat_name(cid)} — {pct}% (6–7), оценок: {total_n}")
    if most_dry is None:
        lines.append(f"- 🥨 Самый сухой чат: недостаточно данных (нужно минимум {min_samples} оценок)")
    else:
        cid, share, _dry_n, total_n = most_dry
        pct = int(round(float(share) * 100))
        lines.append(f"- 🥨 Самый сухой чат: {chat_name(cid)} — {pct}% (1–2), оценок: {total_n}")

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
