from __future__ import annotations

from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.debug_content import action_label, explain_text, holiday_text
from app.bot.keyboards.debug import debug_explain_kb, debug_kb, debug_recap_nav_kb
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.reminder_service import build_late_reminder_text
from app.services.repo_service import get_or_create_session, get_session_message_id, upsert_chat
from app.services.scheduler_reports import build_periodic_report_text
from app.services.stats_service import build_stats_raw_debug_text
from app.services.time_service import get_session_window, now_in_tz
from app.services.q1_service import render_q1
from app.services.q2_q3_service import render_q2_text, render_q3_text
from app.services.recap_service import (
    build_chat_year_recap_cards,
    build_my_year_recap_cards,
    build_my_year_recap_cards_all_chats,
    list_user_member_chat_ids,
    recap_target_year,
)

router = Router()

_engine = None
_session_factory = None
_debug_last_by_chat: dict[int, str] = {}


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(database_url)
        _session_factory = make_session_factory(_engine)


def _is_owner(settings, user_id: int) -> bool:
    return settings.bot_owner_id is not None and int(settings.bot_owner_id) == int(user_id)


def _actor_label(cb: CallbackQuery) -> str:
    if cb.from_user is None:
        return "unknown"
    if cb.from_user.username:
        return f"@{cb.from_user.username}"
    return f"id:{cb.from_user.id}"


def _build_menu_text(today: date | None, chat_id: int, mode: str) -> str:
    mode_ru = "Превью" if mode == "preview" else "Отправка"
    base = (
        "🛠 Debug-меню\n"
        f"Дата чата: {today.strftime('%d.%m.%y') if today else '-'}\n"
        f"Режим: {mode_ru}\n"
        "Выбери событие для принудительного теста."
    )
    last_line = _debug_last_by_chat.get(chat_id)
    if not last_line:
        return base + "\n\nПоследнее действие: нет"
    return base + f"\n\nПоследнее действие:\n{last_line}"


def _remember_last_action(chat_id: int, actor: str, action: str, mode: str) -> None:
    mode_ru = "Превью" if mode == "preview" else "Отправка"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _debug_last_by_chat[chat_id] = f"{ts} • {actor} • {action_label(action)} • {mode_ru}"


async def _send_output(
    cb: CallbackQuery,
    mode: str,
    text: str,
    explain_action: str | None = None,
    *,
    parse_mode: str | None = None,
    reply_to_message_id: int | None = None,
) -> None:
    if cb.message is None:
        return
    kb = debug_explain_kb(explain_action) if explain_action else None
    if mode == "preview":
        await cb.message.answer(f"🔎 Превью\n\n{text}", parse_mode=parse_mode, reply_markup=kb)
        return
    await cb.message.answer(text, parse_mode=parse_mode, reply_to_message_id=reply_to_message_id, reply_markup=kb)


def _resolve_source_chat_id(current_chat_id: int, member_chat_ids: list[int]) -> int | None:
    if current_chat_id < 0:
        return current_chat_id
    return member_chat_ids[0] if member_chat_ids else None


async def _send_recap_cards(cb: CallbackQuery, mode: str, cards: list[str], explain_action: str) -> None:
    if cb.message is None:
        return
    if not cards:
        await _send_output(cb, mode, "Карточки рекапа пустые.")
        return
    await _send_output(
        cb,
        mode,
        f"Карточка 1/{len(cards)}\n\n{cards[0]}",
        explain_action=explain_action,
    )


def _format_debug_card(mode: str, idx: int, total: int, card: str) -> str:
    body = f"Карточка {idx}/{total}\n\n{card}"
    if mode == "preview":
        return f"🔎 Превью\n\n{body}"
    return body


def _nav_or_none(mode: str, kind: str, source_chat_id: int, year: int, current_index: int, total_cards: int):
    if total_cards <= 1:
        return None
    return debug_recap_nav_kb(
        mode=mode,
        kind=kind,
        source_chat_id=source_chat_id,
        year=year,
        current_index=current_index,
        total_cards=total_cards,
    )


async def _send_debug_action(cb: CallbackQuery, action: str, mode: str) -> bool:
    from app.core.config import load_settings

    settings = load_settings()
    if cb.from_user is None or not _is_owner(settings, cb.from_user.id):
        return False
    if cb.message is None:
        return False

    init_db(settings.database_url)
    chat_id = cb.message.chat.id

    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=chat_id)
        window = get_session_window(chat.timezone)
        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)
        q1_msg_id = get_session_message_id(db, sess.session_id, "Q1")
        member_chat_ids = list_user_member_chat_ids(db, cb.from_user.id)
        year = recap_target_year(window.session_date)

        if action == "q1":
            text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=window.session_date)
            await _send_output(cb, mode, text, explain_action=action)
            return True

        if action == "q2q3":
            await _send_output(cb, mode, render_q2_text(db, chat_id, sess.session_id), explain_action=action)
            await _send_output(cb, mode, render_q3_text(db, chat_id, sess.session_id), explain_action=action)
            return True

        if action == "late":
            text = build_late_reminder_text(db, sess.session_id) or "⏳ Финальная напоминалка неактуальна."
            await _send_output(cb, mode, text, explain_action=action, parse_mode="HTML", reply_to_message_id=q1_msg_id or None)
            return True

        if action == "week":
            text = build_periodic_report_text(db, chat_id=chat_id, local_date=window.session_date, period="week", title="📉 Итоги недели")
            await _send_output(cb, mode, text, explain_action=action)
            return True

        if action == "month":
            text = build_periodic_report_text(db, chat_id=chat_id, local_date=window.session_date, period="month", title="📉 Итоги месяца")
            await _send_output(cb, mode, text, explain_action=action)
            return True

        if action == "year":
            text = build_periodic_report_text(db, chat_id=chat_id, local_date=window.session_date, period="year", title="📉 Итоги года")
            await _send_output(cb, mode, text, explain_action=action)
            return True

        if action == "stats_raw":
            text = build_stats_raw_debug_text(db, chat_id=chat_id, user_id=cb.from_user.id, today=window.session_date)
            await _send_output(cb, mode, text, explain_action=action)
            return True

        if action == "recap_announce":
            recap_text = (
                "🎉 Доступен рекап года.\nЗапустить можно этой кнопкой или через `/stats`."
                if chat_id > 0
                else "🎉 Доступен рекап года. Забирай итоги!"
            )
            await _send_output(cb, mode, recap_text, explain_action=action)
            return True

        if action == "recap_chat":
            source_chat_id = _resolve_source_chat_id(chat_id, member_chat_ids)
            if source_chat_id is None:
                await _send_output(cb, mode, "Нет доступного группового чата для чат-рекапа.")
                return True
            cards = build_chat_year_recap_cards(db, chat_id=source_chat_id, year=year)
            text = _format_debug_card(mode, 1, len(cards), cards[0]) if cards else "Карточки рекапа пустые."
            kb = _nav_or_none(mode, "chat", source_chat_id, year, current_index=0, total_cards=len(cards))
            await cb.message.answer(text, reply_markup=kb)
            if cards:
                await cb.message.answer("ℹ️ Нажми кнопку ниже, чтобы узнать условия показа.", reply_markup=debug_explain_kb(action))
            return True

        if action == "recap_my_chat":
            source_chat_id = _resolve_source_chat_id(chat_id, member_chat_ids)
            if source_chat_id is None:
                await _send_output(cb, mode, "Нет доступного группового чата для личного рекапа.")
                return True
            cards = build_my_year_recap_cards(db, chat_id=source_chat_id, user_id=cb.from_user.id, year=year)
            text = _format_debug_card(mode, 1, len(cards), cards[0]) if cards else "Карточки рекапа пустые."
            kb = _nav_or_none(mode, "mychat", source_chat_id, year, current_index=0, total_cards=len(cards))
            await cb.message.answer(text, reply_markup=kb)
            if cards:
                await cb.message.answer("ℹ️ Нажми кнопку ниже, чтобы узнать условия показа.", reply_markup=debug_explain_kb(action))
            return True

        if action == "recap_my_all":
            cards = build_my_year_recap_cards_all_chats(db, user_id=cb.from_user.id, year=year)
            text = _format_debug_card(mode, 1, len(cards), cards[0]) if cards else "Карточки рекапа пустые."
            kb = _nav_or_none(mode, "myall", 0, year, current_index=0, total_cards=len(cards))
            await cb.message.answer(text, reply_markup=kb)
            if cards:
                await cb.message.answer("ℹ️ Нажми кнопку ниже, чтобы узнать условия показа.", reply_markup=debug_explain_kb(action))
            return True

        if action.startswith("holiday:"):
            kind = action.split(":")[1]
            await _send_output(cb, mode, holiday_text(kind), explain_action=action)
            return True

        if action == "all":
            sub_actions = ("q1", "q2q3", "late", "week", "month", "year", "stats_raw", "holiday:feb9", "holiday:nov19", "recap_announce")
            done: list[str] = []
            for sub_action in sub_actions:
                ok = await _send_debug_action(cb, sub_action, mode)
                if ok:
                    done.append(action_label(sub_action))
            summary_lines = [
                "✅ Прогон завершен.",
                f"Режим: {'Превью' if mode == 'preview' else 'Отправка'}",
                f"Успешно: {len(done)}/{len(sub_actions)}",
            ]
            if done:
                summary_lines.append("Что выполнено:")
                summary_lines.extend([f"- {label}" for label in done])
            await cb.message.answer("\n".join(summary_lines))
            return True

    return False


@router.message(Command("debug"))
async def debug_cmd(message: Message) -> None:
    if message.chat is None or message.from_user is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    if not _is_owner(settings, message.from_user.id):
        return

    init_db(settings.database_url)
    today: date | None = None
    with db_session(_session_factory) as db:
        chat = upsert_chat(db, chat_id=message.chat.id)
        today = now_in_tz(chat.timezone).date()

    await message.answer(
        _build_menu_text(today=today, chat_id=message.chat.id, mode="preview"),
        reply_markup=debug_kb(mode="preview"),
    )


@router.callback_query(F.data.startswith("debug:"))
async def debug_callbacks(cb: CallbackQuery) -> None:
    if cb.data is None:
        return

    parts = cb.data.split(":")
    if len(parts) < 3:
        return

    from app.core.config import load_settings

    settings = load_settings()
    if cb.from_user is None or not _is_owner(settings, cb.from_user.id):
        return
    init_db(settings.database_url)

    if parts[1] == "mode" and len(parts) == 3:
        mode = parts[2]
        if mode not in {"preview", "send"}:
            return
        if cb.message is not None:
            chat_id = cb.message.chat.id
            with db_session(_session_factory) as db:
                chat = upsert_chat(db, chat_id=chat_id)
                today = now_in_tz(chat.timezone).date()
            await cb.message.edit_text(_build_menu_text(today=today, chat_id=chat_id, mode=mode), reply_markup=debug_kb(mode=mode))
        await cb.answer()
        return

    if parts[1] == "refresh" and len(parts) == 3:
        mode = parts[2]
        if mode not in {"preview", "send"}:
            return
        if cb.message is not None:
            chat_id = cb.message.chat.id
            with db_session(_session_factory) as db:
                chat = upsert_chat(db, chat_id=chat_id)
                today = now_in_tz(chat.timezone).date()
            await cb.message.edit_text(_build_menu_text(today=today, chat_id=chat_id, mode=mode), reply_markup=debug_kb(mode=mode))
        await cb.answer()
        return

    if parts[1] == "back" and len(parts) == 3:
        mode = parts[2]
        if mode not in {"preview", "send"}:
            return
        if cb.message is not None:
            chat_id = cb.message.chat.id
            with db_session(_session_factory) as db:
                chat = upsert_chat(db, chat_id=chat_id)
                today = now_in_tz(chat.timezone).date()
            await cb.message.edit_text(
                _build_menu_text(today=today, chat_id=chat_id, mode=mode),
                reply_markup=debug_kb(mode=mode),
            )
        await cb.answer()
        return

    if parts[1] == "run" and len(parts) >= 4:
        mode = parts[2]
        if mode not in {"preview", "send"}:
            return
        action = ":".join(parts[3:])
        handled = await _send_debug_action(cb, action, mode)
        if handled:
            if cb.message is not None:
                chat_id = cb.message.chat.id
                _remember_last_action(chat_id=chat_id, actor=_actor_label(cb), action=action, mode=mode)
            await cb.answer()
        return

    if parts[1] == "card" and len(parts) == 7:
        mode = parts[2]
        kind = parts[3]
        try:
            source_chat_id = int(parts[4])
            year = int(parts[5])
            idx = int(parts[6])
        except ValueError:
            return
        if mode not in {"preview", "send"}:
            return
        if cb.message is None or cb.from_user is None:
            return

        with db_session(_session_factory) as db:
            if kind == "chat":
                cards = build_chat_year_recap_cards(db, chat_id=source_chat_id, year=year)
            elif kind == "mychat":
                cards = build_my_year_recap_cards(db, chat_id=source_chat_id, user_id=cb.from_user.id, year=year)
            elif kind == "myall":
                cards = build_my_year_recap_cards_all_chats(db, user_id=cb.from_user.id, year=year)
            else:
                return

        if not cards:
            await cb.answer()
            return
        if idx < 0 or idx >= len(cards):
            await cb.answer()
            return

        text = _format_debug_card(mode, idx + 1, len(cards), cards[idx])
        kb = _nav_or_none(mode, kind, source_chat_id, year, current_index=idx, total_cards=len(cards))
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()
        return

    if parts[1] == "explain" and len(parts) >= 3:
        action = ":".join(parts[2:])
        await cb.message.answer(explain_text(action))
        await cb.answer()
