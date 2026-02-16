from __future__ import annotations

from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.debug import debug_kb
from app.db.engine import make_engine, make_session_factory
from app.db.session import db_session
from app.services.reminder_service import build_late_reminder_text, build_reminder_22_text
from app.services.repo_service import get_or_create_session, get_session_message_id, upsert_chat
from app.services.scheduler_service import build_periodic_report_text
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


def _holiday_text(kind: str) -> str:
    if kind == "feb9":
        return "Сегодня Национальный день какашек (National Poop Day)."
    if kind == "nov19":
        return "Сегодня Всемирный день туалета (World Toilet Day)."
    return "Holiday notice не найден."


def _action_label(action: str) -> str:
    labels = {
        "q1": "Q1 автопост",
        "q2q3": "Q2/Q3",
        "r22": "Напоминалка 22:00",
        "late": "Финалка 23:30",
        "week": "Итоги недели",
        "month": "Итоги месяца",
        "year": "Итоги года",
        "recap_announce": "Анонс рекапа",
        "recap_chat": "Рекап чата",
        "recap_my_chat": "Рекап личный (текущий чат)",
        "recap_my_all": "Рекап личный (все чаты)",
        "holiday:feb9": "Holiday 9 Feb",
        "holiday:nov19": "Holiday 19 Nov",
        "all": "Прогнать все",
    }
    return labels.get(action, action)


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
    _debug_last_by_chat[chat_id] = f"{ts} • {actor} • {_action_label(action)} • {mode_ru}"


async def _send_output(
    cb: CallbackQuery,
    mode: str,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_to_message_id: int | None = None,
) -> None:
    if cb.message is None:
        return
    if mode == "preview":
        await cb.message.answer(f"🔎 Превью\n\n{text}", parse_mode=parse_mode)
        return
    await cb.message.answer(text, parse_mode=parse_mode, reply_to_message_id=reply_to_message_id)


def _resolve_source_chat_id(current_chat_id: int, member_chat_ids: list[int]) -> int | None:
    if current_chat_id < 0:
        return current_chat_id
    return member_chat_ids[0] if member_chat_ids else None


async def _send_recap_cards(cb: CallbackQuery, mode: str, cards: list[str]) -> None:
    if cb.message is None:
        return
    if not cards:
        await _send_output(cb, mode, "Карточки рекапа пустые.")
        return
    if mode == "preview":
        await _send_output(cb, mode, f"Карточек: {len(cards)}\n\nКарточка 1/{len(cards)}\n\n{cards[0]}")
        return
    for idx, card in enumerate(cards, start=1):
        await cb.message.answer(f"Карточка {idx}/{len(cards)}\n\n{card}")


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
            await _send_output(cb, mode, text)
            return True

        if action == "q2q3":
            await _send_output(cb, mode, render_q2_text(db, chat_id, sess.session_id))
            await _send_output(cb, mode, render_q3_text(db, chat_id, sess.session_id))
            return True

        if action == "r22":
            text = build_reminder_22_text(db, sess.session_id) or "⏰ Напоминалка 22:00 неактуальна."
            await _send_output(cb, mode, text, parse_mode="HTML", reply_to_message_id=q1_msg_id or None)
            return True

        if action == "late":
            text = build_late_reminder_text(db, sess.session_id) or "⏳ Финальная напоминалка неактуальна."
            await _send_output(cb, mode, text, parse_mode="HTML", reply_to_message_id=q1_msg_id or None)
            return True

        if action == "week":
            text = build_periodic_report_text(db, chat_id=chat_id, local_date=window.session_date, period="week", title="📉 Итоги недели")
            await _send_output(cb, mode, text)
            return True

        if action == "month":
            text = build_periodic_report_text(db, chat_id=chat_id, local_date=window.session_date, period="month", title="📉 Итоги месяца")
            await _send_output(cb, mode, text)
            return True

        if action == "year":
            text = build_periodic_report_text(db, chat_id=chat_id, local_date=window.session_date, period="year", title="📉 Итоги года")
            await _send_output(cb, mode, text)
            return True

        if action == "recap_announce":
            recap_text = (
                "🎉 Доступен рекап года.\nЗапустить можно этой кнопкой или через `/stats`."
                if chat_id > 0
                else "🎉 Доступен рекап года. Забирай итоги!"
            )
            await _send_output(cb, mode, recap_text)
            return True

        if action == "recap_chat":
            source_chat_id = _resolve_source_chat_id(chat_id, member_chat_ids)
            if source_chat_id is None:
                await _send_output(cb, mode, "Нет доступного группового чата для чат-рекапа.")
                return True
            cards = build_chat_year_recap_cards(db, chat_id=source_chat_id, year=year)
            await _send_recap_cards(cb, mode, cards)
            return True

        if action == "recap_my_chat":
            source_chat_id = _resolve_source_chat_id(chat_id, member_chat_ids)
            if source_chat_id is None:
                await _send_output(cb, mode, "Нет доступного группового чата для личного рекапа.")
                return True
            cards = build_my_year_recap_cards(db, chat_id=source_chat_id, user_id=cb.from_user.id, year=year)
            await _send_recap_cards(cb, mode, cards)
            return True

        if action == "recap_my_all":
            cards = build_my_year_recap_cards_all_chats(db, user_id=cb.from_user.id, year=year)
            await _send_recap_cards(cb, mode, cards)
            return True

        if action.startswith("holiday:"):
            kind = action.split(":")[1]
            await _send_output(cb, mode, _holiday_text(kind))
            return True

        if action == "all":
            for sub_action in ("q1", "q2q3", "r22", "late", "week", "month", "year", "holiday:feb9", "holiday:nov19", "recap_announce"):
                await _send_debug_action(cb, sub_action, mode)
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
