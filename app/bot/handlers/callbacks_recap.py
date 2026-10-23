from __future__ import annotations

from app.core.security_audit import telegram_denied

from html import escape

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.keyboards.recap import recap_chat_card_kb, recap_chat_pick_mode_kb, recap_entry_kb, recap_next_kb
from app.db.runtime import get_session_factory
from app.db.session import db_session
from app.services.recap_service import (
    build_chat_year_recap_cards,
    build_my_year_recap_cards,
    build_my_year_recap_cards_all_chats,
    is_recap_available,
    list_user_member_chat_ids,
    list_user_recap_chat_ids,
    recap_target_year,
)
from app.services.time_service import now_in_tz

logger = logging.getLogger(__name__)
router = Router()

_session_factory = None

GROUP_MENU_TEXT = (
    "🎉 Рекап года\n\n"
    "Выбери режим:\n"
    "• `📊 Рекап чата` — карточки итогов этого чата прямо здесь.\n"
    "• `🎉 Личный рекап` — отправим твои карточки в личку с ботом.\n\n"
    "Если личка не открыта: открой бота, нажми `/start` и повтори запуск."
)

OWNER_PRIVATE_MENU_TEXT = (
    "🎉 Рекап года\n\n"
    "Режим владельца:\n"
    "• `📊 Рекап чата` — выбрать любой чат, где ты участник, и запустить чат-рекап.\n"
    "• `🎉 Личный рекап` — выбрать любой чат, где ты участник, и запустить личный рекап."
)


def init_db(database_url: str) -> None:
    global _session_factory
    _session_factory = get_session_factory(database_url)


def _is_owner(settings, user_id: int) -> bool:
    return settings.bot_owner_id is not None and int(settings.bot_owner_id) == int(user_id)


def _format_chat_title(raw: str, fallback_id: int) -> str:
    title = (raw or "").strip()
    if not title:
        title = f"Чат {fallback_id}"
    return title[:48]


def _user_ping(cb: CallbackQuery) -> str:
    if cb.from_user is None:
        return "Пользователь"
    if cb.from_user.username:
        return escape(f"@{cb.from_user.username}")
    name = cb.from_user.full_name or "Пользователь"
    safe_name = escape(name)
    return f'<a href="tg://user?id={cb.from_user.id}">{safe_name}</a>'


async def _notify_open_dm_in_group(cb: CallbackQuery) -> None:
    if cb.message is None:
        return
    text = (
        f"{_user_ping(cb)}, открой личку с ботом и нажми /start, "
        "потом снова нажми «🎉 Личный рекап»."
    )
    await cb.bot.send_message(
        chat_id=cb.message.chat.id,
        text=text,
        reply_to_message_id=cb.message.message_id,
    )


async def _resolve_chat_options(cb: CallbackQuery, chat_ids: list[int]) -> list[tuple[int, str]]:
    options: list[tuple[int, str]] = []
    for cid in chat_ids:
        try:
            chat = await cb.bot.get_chat(cid)
            title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or ""
        except Exception:
            title = ""
        options.append((cid, _format_chat_title(title, cid)))
    return options


async def _enrich_chat_titles(cards: list[str], cb: CallbackQuery) -> list[str]:
    ids = {int(x) for x in re.findall(r"Чат (-?\d+)", "\n".join(cards))}
    if not ids:
        return cards
    titles: dict[int, str] = {}
    for cid in ids:
        try:
            chat = await cb.bot.get_chat(cid)
            title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or f"Чат {cid}"
            titles[cid] = escape(str(title).strip())
        except Exception:
            titles[cid] = f"Чат {cid}"

    out: list[str] = []
    for card in cards:
        text = card
        for cid, title in titles.items():
            text = text.replace(f"Чат {cid}", title)
        out.append(text)
    return out


async def _send_personal_recap_to_dm(cb: CallbackQuery, source_chat_id: int, year: int) -> bool:
    if cb.from_user is None:
        return False
    with db_session(_session_factory) as db:
        cards = build_my_year_recap_cards(db, chat_id=source_chat_id, user_id=cb.from_user.id, year=year)
    text = f"Карточка 1/{len(cards)}\n\n{cards[0]}"
    kb = recap_next_kb(source_chat_id, year, 1) if len(cards) > 1 else None
    try:
        await cb.bot.send_message(chat_id=cb.from_user.id, text=text, reply_markup=kb)
        return True
    except Exception:
        return False


async def _send_personal_recap_all_chats_to_dm(cb: CallbackQuery, year: int) -> bool:
    if cb.from_user is None:
        return False
    with db_session(_session_factory) as db:
        cards = build_my_year_recap_cards_all_chats(db, user_id=cb.from_user.id, year=year)
    cards = await _enrich_chat_titles(cards, cb)
    text = f"Карточка 1/{len(cards)}\n\n{cards[0]}"
    kb = recap_next_kb(0, year, 1) if len(cards) > 1 else None
    try:
        await cb.bot.send_message(chat_id=cb.from_user.id, text=text, reply_markup=kb)
        return True
    except Exception:
        return False


async def _send_group_chat_recap_start(cb: CallbackQuery, source_chat_id: int, year: int) -> None:
    with db_session(_session_factory) as db:
        cards = build_chat_year_recap_cards(db, chat_id=source_chat_id, year=year)
    text = f"Карточка 1/{len(cards)}\n\n{cards[0]}"
    kb = recap_chat_card_kb(source_chat_id=source_chat_id, year=year, next_index=1, has_next=len(cards) > 1)
    await cb.bot.send_message(chat_id=cb.message.chat.id, text=text, reply_markup=kb)


async def _check_recap_window(cb: CallbackQuery) -> tuple[bool, int, object] | tuple[bool, None, object]:
    from app.core.config import load_settings
    from app.db.models import Chat

    settings = load_settings()
    init_db(settings.database_url)

    with db_session(_session_factory) as db:
        source_chat = db.get(Chat, cb.message.chat.id)
        tz = source_chat.timezone if source_chat else "Europe/Minsk"
        today = now_in_tz(tz).date()

    if not is_recap_available(today, cb.from_user.id, settings.bot_owner_id):
        telegram_denied(cb, "recap", "outside_recap_window")
        await cb.answer("Рекап доступен с 30 декабря по 3 января", show_alert=True)
        return False, None, settings

    return True, recap_target_year(today), settings


@router.callback_query(F.data == "stats:open:recap")
async def recap_open(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    ok, year, settings = await _check_recap_window(cb)
    if not ok:
        return

    owner = _is_owner(settings, cb.from_user.id)

    if owner and cb.message.chat.type != "private":
        await cb.answer("Для владельца рекап доступен только в личке с ботом", show_alert=True)
        return

    if cb.message.chat.type == "private":
        if owner:
            await cb.message.edit_text(OWNER_PRIVATE_MENU_TEXT, reply_markup=recap_entry_kb())
            await cb.answer()
            return

        sent = await _send_personal_recap_all_chats_to_dm(cb, int(year))
        if not sent:
            await cb.answer("Открой личку с ботом и нажми /start, потом повтори", show_alert=True)
            return
        await cb.answer()
        return

    await cb.message.edit_text(GROUP_MENU_TEXT, reply_markup=recap_entry_kb())
    await cb.answer()


@router.callback_query(F.data == "recap:entry:menu")
async def recap_entry_menu(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    ok, _, settings = await _check_recap_window(cb)
    if not ok:
        return

    owner = _is_owner(settings, cb.from_user.id)
    if owner and cb.message.chat.type != "private":
        await cb.answer("Для владельца рекап доступен только в личке с ботом", show_alert=True)
        return
    if cb.message.chat.type == "private" and owner:
        await cb.message.edit_text(OWNER_PRIVATE_MENU_TEXT, reply_markup=recap_entry_kb())
    else:
        await cb.message.edit_text(GROUP_MENU_TEXT, reply_markup=recap_entry_kb())
    await cb.answer()


@router.callback_query(F.data == "recap:entry:chat")
async def recap_entry_chat(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    ok, year, settings = await _check_recap_window(cb)
    if not ok:
        return

    owner = _is_owner(settings, cb.from_user.id)

    if owner and cb.message.chat.type != "private":
        await cb.answer("Для владельца рекап доступен только в личке с ботом", show_alert=True)
        return

    if cb.message.chat.type != "private":
        await _send_group_chat_recap_start(cb, cb.message.chat.id, int(year))
        await cb.answer()
        return

    if not owner:
        await cb.answer("Открой рекап из группового чата", show_alert=True)
        return

    with db_session(_session_factory) as db:
        chat_ids = list_user_member_chat_ids(db, cb.from_user.id)

    if not chat_ids:
        await cb.answer("Нет чатов, где ты участник", show_alert=True)
        return

    options = await _resolve_chat_options(cb, chat_ids)
    await cb.message.edit_text(
        f"📊 Рекап чата {int(year)}\n\nВыбери чат:",
        reply_markup=recap_chat_pick_mode_kb(year=int(year), mode="chat", chat_options=options),
    )
    await cb.answer()


@router.callback_query(F.data == "recap:entry:personal")
async def recap_entry_personal(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return

    ok, year, settings = await _check_recap_window(cb)
    if not ok:
        return

    owner = _is_owner(settings, cb.from_user.id)

    if owner and cb.message.chat.type != "private":
        await cb.answer("Для владельца рекап доступен только в личке с ботом", show_alert=True)
        return

    if cb.message.chat.type != "private":
        sent = await _send_personal_recap_to_dm(cb, cb.message.chat.id, int(year))
        if not sent:
            await _notify_open_dm_in_group(cb)
            await cb.answer("Открой личку с ботом и нажми /start", show_alert=True)
            return
        await cb.answer("Отправил в личку", show_alert=False)
        return

    if not owner:
        sent = await _send_personal_recap_all_chats_to_dm(cb, int(year))
        if not sent:
            await cb.answer("Открой личку с ботом и нажми /start, потом повтори", show_alert=True)
            return
        await cb.answer()
        return

    with db_session(_session_factory) as db:
        chat_ids = list_user_member_chat_ids(db, cb.from_user.id)

    if not chat_ids:
        await cb.answer("Нет чатов, где ты участник", show_alert=True)
        return

    options = await _resolve_chat_options(cb, chat_ids)
    options.insert(0, (0, "🌐 По всем чатам"))
    await cb.message.edit_text(
        f"🎉 Личный рекап {int(year)}\n\nВыбери чат:",
        reply_markup=recap_chat_pick_mode_kb(year=int(year), mode="personal", chat_options=options),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("recap:pick:"))
async def recap_pick_chat(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None or cb.data is None:
        return

    from app.core.config import load_settings

    settings = load_settings()
    init_db(settings.database_url)

    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Неактуально", show_alert=False)
        return

    mode = parts[2]
    try:
        source_chat_id = int(parts[3])
        year = int(parts[4])
    except ValueError:
        await cb.answer("Неактуально", show_alert=False)
        return

    owner = _is_owner(settings, cb.from_user.id)
    if not owner or cb.message.chat.type != "private":
        telegram_denied(cb, "recap_picker", "owner_private_required")
        await cb.answer("Неактуально", show_alert=False)
        return

    with db_session(_session_factory) as db:
        allowed_chat_ids = list_user_member_chat_ids(db, cb.from_user.id)
    if not (mode == "personal" and source_chat_id == 0) and source_chat_id not in allowed_chat_ids:
        telegram_denied(cb, "recap_picker", "chat_scope_rejected")
        await cb.answer("Неактуально", show_alert=False)
        return

    if mode == "chat":
        with db_session(_session_factory) as db:
            cards = build_chat_year_recap_cards(db, chat_id=source_chat_id, year=year)
        text = f"Карточка 1/{len(cards)}\n\n{cards[0]}"
        kb = recap_chat_card_kb(source_chat_id=source_chat_id, year=year, next_index=1, has_next=len(cards) > 1)
        await cb.bot.send_message(chat_id=cb.message.chat.id, text=text, reply_markup=kb)
        await cb.answer()
        return

    if mode == "personal":
        if source_chat_id == 0:
            sent = await _send_personal_recap_all_chats_to_dm(cb, year)
        else:
            sent = await _send_personal_recap_to_dm(cb, source_chat_id, year)
        if not sent:
            await cb.answer("Открой личку с ботом и нажми /start, потом повтори", show_alert=True)
            return
        await cb.answer()
        return

    await cb.answer("Неактуально", show_alert=False)


@router.callback_query(F.data.startswith("recap:chatnext:"))
async def recap_chat_next(cb: CallbackQuery) -> None:
    if cb.message is None or cb.data is None or cb.from_user is None:
        return

    ok, _, settings = await _check_recap_window(cb)
    if not ok:
        return

    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer()
        return

    try:
        source_chat_id = int(parts[2])
        year = int(parts[3])
        idx = int(parts[4])
    except ValueError:
        await cb.answer()
        return

    owner = _is_owner(settings, cb.from_user.id)
    if cb.message.chat.type == "private" and owner:
        with db_session(_session_factory) as db:
            allowed = list_user_member_chat_ids(db, cb.from_user.id)
        if source_chat_id != 0 and source_chat_id not in allowed:
            telegram_denied(cb, "recap_card", "chat_scope_rejected")
            await cb.answer("Неактуально", show_alert=False)
            return
    else:
        if cb.message.chat.id != source_chat_id:
            telegram_denied(cb, "recap_card", "chat_scope_rejected")
            await cb.answer("Неактуально", show_alert=False)
            return

    with db_session(_session_factory) as db:
        cards = build_chat_year_recap_cards(db, chat_id=source_chat_id, year=year)

    if idx < 0 or idx >= len(cards):
        await cb.answer("Рекап завершён", show_alert=False)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        return

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    text = f"Карточка {idx + 1}/{len(cards)}\n\n{cards[idx]}"
    kb = recap_chat_card_kb(source_chat_id=source_chat_id, year=year, next_index=idx + 1, has_next=(idx + 1) < len(cards))
    await cb.bot.send_message(chat_id=cb.message.chat.id, text=text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("recap:next:"))
async def recap_next(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None or cb.data is None:
        return

    ok, _, settings = await _check_recap_window(cb)
    if not ok:
        return

    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer()
        return

    try:
        source_chat_id = int(parts[2])
        year = int(parts[3])
        idx = int(parts[4])
    except ValueError:
        await cb.answer()
        return

    owner = _is_owner(settings, cb.from_user.id)

    if cb.message.chat.type == "private" and owner:
        with db_session(_session_factory) as db:
            allowed = list_user_member_chat_ids(db, cb.from_user.id)
        if source_chat_id != 0 and source_chat_id not in allowed:
            telegram_denied(cb, "recap_card", "chat_scope_rejected")
            await cb.answer("Неактуально", show_alert=False)
            return
    elif cb.message.chat.type == "private":
        if source_chat_id == 0:
            pass
        else:
            with db_session(_session_factory) as db:
                allowed = list_user_recap_chat_ids(db, cb.from_user.id, year)
            if source_chat_id != 0 and source_chat_id not in allowed:
                telegram_denied(cb, "recap_card", "chat_scope_rejected")
                await cb.answer("Неактуально", show_alert=False)
                return

    with db_session(_session_factory) as db:
        if source_chat_id == 0:
            cards = build_my_year_recap_cards_all_chats(db, user_id=cb.from_user.id, year=year)
        else:
            cards = build_my_year_recap_cards(db, chat_id=source_chat_id, user_id=cb.from_user.id, year=year)
    if source_chat_id == 0:
        cards = await _enrich_chat_titles(cards, cb)

    if idx < 0 or idx >= len(cards):
        await cb.answer("Рекап завершён", show_alert=False)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        return

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    text = f"Карточка {idx + 1}/{len(cards)}\n\n{cards[idx]}"
    kb = recap_next_kb(source_chat_id, year, idx + 1) if idx + 1 < len(cards) else None
    await cb.bot.send_message(chat_id=cb.message.chat.id, text=text, reply_markup=kb)
    await cb.answer()
