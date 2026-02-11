from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, date, time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from app.db.models import Chat, Session as DaySession, SessionUserState, ChatMember, User, UserStreak
from app.db.session import db_session
from app.services.repo_service import (
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
)
from app.services.time_service import get_session_window, now_in_tz
from app.services.q1_service import render_q1
from app.services.q2_q3_service import ensure_q2_q3_exist
from app.services.stats_service import build_stats_text_chat
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.bot.keyboards.q1 import q1_keyboard

logger = logging.getLogger(__name__)
_streak_recalc_date: dict[int, date] = {}

LOCK_LINE = "рџ”’ РЎРµСЃСЃРёСЏ Р·Р°РєСЂС‹С‚Р°."

Q2_TEXT = (
    "рџ§» Р‘СЂРёСЃС‚РѕР»СЊ (С‚РёРї СЃС‚СѓР»Р°)\n"
    'РЈР·РЅР°С‚СЊ Рѕ <a href="https://ru.wikipedia.org/wiki/Р‘СЂРёСЃС‚РѕР»СЊСЃРєР°СЏ_С€РєР°Р»Р°_С„РѕСЂРјС‹_РєР°Р»Р°">С€РєР°Р»Рµ Р‘СЂРёСЃС‚РѕР»СЏ</a>\n\n'
    "рџ§± 1вЂ“2 (Р¶С‘СЃС‚РєРѕ / СЃСѓС…Рѕ)\n"
    "рџЌЊ 3вЂ“4 (РЅРѕСЂРјР°)\n"
    "рџЌ¦ 5вЂ“6 (РјСЏРіРєРѕ)\n"
    "рџ’¦ 7 (РІРѕРґРёС‡РєР°)"
)

Q3_TEXT = (
    "рџ®вЂЌрџ’Ё РљР°Рє РїСЂРѕС€С‘Р» РїСЂРѕС†РµСЃСЃ?\n"
    "рџ‡ РџСЂРµРєСЂР°СЃРЅРѕ\n"
    "рџђ РЎРѕР№РґС‘С‚\n"
    "рџ« РЈР¶Р°СЃРЅРѕ"
)


def start_scheduler(bot: Bot, session_factory: sessionmaker) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        func=_tick,
        trigger=IntervalTrigger(seconds=30),
        args=[bot, session_factory],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


async def _safe_sleep_on_retry(exc: Exception) -> bool:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        return False
    try:
        delay = float(retry_after)
    except Exception:
        return False
    delay = max(0.5, min(delay, 30.0))
    logger.warning("Telegram rate limit hit. Sleeping %.1fs", delay)
    await asyncio.sleep(delay)
    return True


async def _safe_send_message(bot: Bot, **kwargs):
    for _ in range(3):
        try:
            return await bot.send_message(**kwargs)
        except Exception as e:
            if await _safe_sleep_on_retry(e):
                continue
            raise


async def _safe_edit_message_text(bot: Bot, **kwargs):
    """
    РџС‹С‚Р°РµРјСЃСЏ 3 СЂР°Р·Р°. РќРµ РІР°Р»РёРјСЃСЏ РЅР°:
    - message is not modified
    - message not found / to edit not found (РєРѕРіРґР° СѓРґР°Р»РёР»Рё СЂСѓРєР°РјРё)
    """
    for _ in range(3):
        try:
            return await bot.edit_message_text(**kwargs)
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                return None
            if "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                return None
            raise
        except Exception as e:
            if await _safe_sleep_on_retry(e):
                continue
            raise


async def _tick(bot: Bot, session_factory: sessionmaker) -> None:
    with db_session(session_factory) as db:
        chats = db.scalars(select(Chat).where(Chat.is_enabled == True)).all()

    for chat in chats:
        try:
            await _process_chat(bot, session_factory, chat.chat_id)
        except TelegramForbiddenError:
            # Bot no longer has access to this chat (kicked/blocked): stop scheduling it.
            with db_session(session_factory) as db:
                stale_chat = db.get(Chat, chat.chat_id)
                if stale_chat is not None:
                    stale_chat.is_enabled = False
            logger.warning("Disabled chat after TelegramForbiddenError chat_id=%s", chat.chat_id)
        except Exception:
            logger.exception("Scheduler chat processing failed chat_id=%s", chat.chat_id)


async def _process_chat(bot: Bot, session_factory: sessionmaker, chat_id: int) -> None:
    with db_session(session_factory) as db:
        chat = db.get(Chat, chat_id)
        if chat is None or not chat.is_enabled:
            return

        window = get_session_window(chat.timezone)
        now_local = now_in_tz(chat.timezone)
        local_time = now_local.time()
        local_date = now_local.date()
        close_cutoff = time(23, 55)

        # Recalculate once per day in a narrow low-traffic window,
        # so daytime polling is not impacted by heavy DB work.
        should_recalc_now = (
            local_time.hour == 0
            and 6 <= local_time.minute <= 10
            and _streak_recalc_date.get(chat_id) != local_date
        )
        if should_recalc_now:
            _recalculate_streaks_from_history(db, chat_id, local_date)
            _streak_recalc_date[chat_id] = local_date
            await _refresh_current_q1_view(bot, db, chat_id, window.session_date)

        active_sessions = db.scalars(
            select(DaySession)
            .where(DaySession.chat_id == chat_id, DaySession.status == "active")
            .order_by(DaySession.session_date.asc())
        ).all()
        for active_sess in active_sessions:
            is_past_day = active_sess.session_date < local_date
            is_today_after_cutoff = active_sess.session_date == local_date and local_time >= close_cutoff
            if is_past_day or is_today_after_cutoff:
                await _close_session(bot, db, chat_id, active_sess.session_id, chat.timezone)

        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        # 23:55 вЂ” Р·Р°РєСЂС‹С‚СЊ
        if local_time >= close_cutoff:
            if sess.status != "closed":
                await _close_session(bot, db, chat_id, sess.session_id, chat.timezone)
            return

        if sess.status == "closed":
            return

        # 23:55вЂ“00:05 (blocked window): РЅРёС‡РµРіРѕ РЅРµ РїРѕСЃС‚РёРј
        if window.is_blocked_window:
            return

        # РђРІС‚РѕРїРѕСЃС‚ Q1 РІ chat.post_time (СЂР°Р±РѕС‚Р°РµС‚ С‚РѕР»СЊРєРѕ РїРѕСЃР»Рµ РїРµСЂРІРѕРіРѕ /start,
        # РїРѕС‚РѕРјСѓ С‡С‚Рѕ Chat РїРѕСЏРІР»СЏРµС‚СЃСЏ РІ Р‘Р” С‚РѕР»СЊРєРѕ РєРѕРіРґР° РµРіРѕ СЃРѕР·РґР°Р»Рё РєРѕРјР°РЅРґРѕР№ /start РёР»Рё /help /stats)
        if local_time.hour == chat.post_time.hour and local_time.minute == chat.post_time.minute:
            q1_id = get_session_message_id(db, sess.session_id, "Q1")
            if not q1_id:
                await _post_q1(bot, db, chat_id, sess.session_id, window.session_date)

        # 22:00 РЅР°РїРѕРјРёРЅР°Р»РєР° (РѕРґРёРЅ СЂР°Р·)
        if local_time.hour == 22 and local_time.minute == 0 and not sess.reminded_22_sent:
            await _send_reminder_22(bot, db, chat_id, sess.session_id)
            sess.reminded_22_sent = True

        # 23:00 РїРµСЂРёРѕРґРёС‡РµСЃРєР°СЏ СЃС‚Р°С‚РёСЃС‚РёРєР° (РЅРµРґРµР»СЏ/РјРµСЃСЏС†/РіРѕРґ)
        if local_time.hour == 23 and local_time.minute == 0:
            await _send_periodic_stats(bot, db, chat_id, local_date)

        await _send_holiday_notice_if_needed(bot, db, chat_id, sess.session_id, local_date)


async def _post_q1(bot: Bot, db, chat_id: int, session_id: int, session_date) -> None:
    member_count = db.scalar(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == chat_id)) or 0
    has_any_members = member_count > 0

    text = render_q1(db, chat_id=chat_id, session_id=session_id, session_date=session_date)
    sent = await _safe_send_message(bot, chat_id=chat_id, text=text, reply_markup=q1_keyboard(has_any_members))
    set_session_message_id(db, session_id, "Q1", sent.message_id)
    await ensure_q2_q3_exist(bot, db, chat_id, session_id)
    logger.info("Auto-posted Q1 chat_id=%s session_id=%s message_id=%s", chat_id, session_id, sent.message_id)


async def _send_reminder_22(bot: Bot, db, chat_id: int, session_id: int) -> None:
    q1_id = get_session_message_id(db, session_id, "Q1")
    if not q1_id:
        return

    subs = db.scalars(
        select(SessionUserState).where(SessionUserState.session_id == session_id, SessionUserState.remind_22 == True)
    ).all()
    if not subs:
        return

    user_ids = [s.user_id for s in subs]
    users = db.scalars(select(User).where(User.user_id.in_(user_ids))).all()
    mentions: list[str] = []
    for u in users:
        if u.username:
            mentions.append(f"@{u.username}")
            continue
        full_name = " ".join(
            part for part in [(u.first_name or "").strip(), (u.last_name or "").strip()] if part
        ).strip()
        if not full_name:
            full_name = "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ"
        mentions.append(f'<a href="tg://user?id={u.user_id}">{full_name}</a>')

    text = "вЏ° Рђ РІРѕС‚ Рё 22:00. РќСѓ С‡С‚Рѕ СЂРµР±СЏС‚Р°, РїРѕРєР°РєР°Р»Рё?\n" + "\n".join(mentions)
    await _safe_send_message(bot, chat_id=chat_id, text=text, reply_to_message_id=q1_id)
    logger.info("Sent 22:00 reminder chat_id=%s session_id=%s", chat_id, session_id)


async def _close_session(bot: Bot, db, chat_id: int, session_id: int, tz_name: str) -> None:
    sess = db.get(DaySession, session_id)
    if sess is None or sess.status == "closed":
        return

    sess.status = "closed"
    sess.end_at = datetime.utcnow()

    # СЃС‚СЂРёРєРё: РµСЃР»Рё СЃРµРіРѕРґРЅСЏ poops_n > 0 в†’ +1 РґРµРЅСЊ РїРѕРґСЂСЏРґ, РёРЅР°С‡Рµ СЃР±СЂРѕСЃ
    member_rows = db.execute(
        select(UserStreak.user_id, UserStreak).where(UserStreak.chat_id == chat_id)
    ).all()

    states = {
        s.user_id: s
        for s in db.scalars(select(SessionUserState).where(SessionUserState.session_id == session_id)).all()
    }

    local_date = sess.session_date

    for user_id, streak in member_rows:
        poops = states.get(user_id).poops_n if user_id in states else 0
        if poops > 0:
            if streak.last_poop_date == (local_date - timedelta(days=1)):
                streak.current_streak += 1
            else:
                streak.current_streak = 1
            streak.last_poop_date = local_date
        else:
            streak.current_streak = 0

    # Р»РѕС‡РёРј Q1/Q2/Q3 (РµСЃР»Рё СЃРѕРѕР±С‰РµРЅРёР№ РЅРµС‚ вЂ” СЃРїРѕРєРѕР№РЅРѕ РїСЂРѕРїСѓСЃРєР°РµРј)
    await _lock_q1(bot, db, chat_id, session_id)
    await _lock_simple(bot, db, chat_id, session_id, "Q2", Q2_TEXT)
    await _lock_simple(bot, db, chat_id, session_id, "Q3", Q3_TEXT)

    logger.info("Closed session chat_id=%s session_id=%s", chat_id, session_id)


async def _lock_q1(bot: Bot, db, chat_id: int, session_id: int) -> None:
    mid = get_session_message_id(db, session_id, "Q1")
    if not mid:
        return

    sess = db.get(DaySession, session_id)
    text = render_q1(db, chat_id=chat_id, session_id=session_id, session_date=sess.session_date)
    text = f"{LOCK_LINE}\n\n{text}"
    await _safe_edit_message_text(bot, chat_id=chat_id, message_id=mid, text=text, reply_markup=None)


async def _lock_simple(bot: Bot, db, chat_id: int, session_id: int, kind: str, body_text: str) -> None:
    mid = get_session_message_id(db, session_id, kind)
    if not mid:
        return
    text = f"{LOCK_LINE}\n\n{body_text}"
    await _safe_edit_message_text(bot, chat_id=chat_id, message_id=mid, text=text, reply_markup=None)


async def _refresh_current_q1_view(bot: Bot, db, chat_id: int, session_date: date) -> None:
    sess = db.scalar(
        select(DaySession).where(
            DaySession.chat_id == chat_id,
            DaySession.session_date == session_date,
        )
    )
    if sess is None or sess.status == "closed":
        return

    q1_id = get_session_message_id(db, sess.session_id, "Q1")
    if not q1_id:
        return

    text = render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=session_date)
    has_any_members = "РЈС‡Р°СЃС‚РЅРёРєРё:" in text
    await _safe_edit_message_text(
        bot,
        chat_id=chat_id,
        message_id=q1_id,
        text=text,
        reply_markup=q1_keyboard(has_any_members),
    )


def _recalculate_streaks_from_history(db, chat_id: int, today: date) -> None:
    member_user_ids = db.scalars(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
    ).all()
    if not member_user_ids:
        return

    rows = db.execute(
        select(DaySession.session_date, SessionUserState.user_id)
        .join(SessionUserState, SessionUserState.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id == chat_id,
            DaySession.session_date < today,
            SessionUserState.poops_n > 0,
            SessionUserState.user_id.in_(member_user_ids),
        )
        .order_by(DaySession.session_date.asc())
    ).all()

    days_by_user: dict[int, list[date]] = {int(uid): [] for uid in member_user_ids}
    for session_date, user_id in rows:
        uid = int(user_id)
        day = session_date
        if not days_by_user[uid] or days_by_user[uid][-1] != day:
            days_by_user[uid].append(day)

    yesterday = today - timedelta(days=1)
    for uid in member_user_ids:
        uid_int = int(uid)
        streak = db.get(UserStreak, {"chat_id": chat_id, "user_id": uid_int})
        if streak is None:
            streak = UserStreak(chat_id=chat_id, user_id=uid_int, current_streak=0, last_poop_date=None)
            db.add(streak)

        days = days_by_user[uid_int]
        if not days:
            streak.current_streak = 0
            streak.last_poop_date = None
            continue

        last_day = days[-1]
        trailing = 1
        idx = len(days) - 2
        while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
            trailing += 1
            idx -= 1

        streak.last_poop_date = last_day
        streak.current_streak = trailing if last_day == yesterday else 0


def _is_last_day_of_month(d: date) -> bool:
    return (d + timedelta(days=1)).month != d.month


async def _send_periodic_stats(bot: Bot, db, chat_id: int, local_date: date) -> None:
    # РёСЃРїРѕР»СЊР·СѓРµРј user_id=0 РєР°Рє СЃРёСЃС‚РµРјРЅСѓСЋ РјРµС‚РєСѓ, С‡С‚РѕР±С‹ РЅРµ РґСѓР±Р»РёСЂРѕРІР°С‚СЊ РѕС‚РїСЂР°РІРєРё
    def _already_sent(kind: str) -> bool:
        return get_command_message_id(db, chat_id, 0, kind, local_date) is not None

    async def _send(kind: str, period: str, title: str) -> None:
        if _already_sent(kind):
            return
        text = title + "\n\n" + build_stats_text_chat(db, chat_id, local_date, period)
        sent = await _safe_send_message(bot, chat_id=chat_id, text=text)
        set_command_message_id(db, chat_id, 0, kind, local_date, sent.message_id)

    # РЅРµРґРµР»СЏ: СЃС‡РёС‚Р°РµРј РєРѕРЅС†РѕРј РЅРµРґРµР»Рё РІРѕСЃРєСЂРµСЃРµРЅСЊРµ (weekday=6)
    if local_date.weekday() == 6:
        await _send("weekly_stats", "week", "рџ“Љ РС‚РѕРіРё РЅРµРґРµР»Рё")

    # РјРµСЃСЏС†
    if _is_last_day_of_month(local_date):
        await _send("monthly_stats", "month", "рџ“Љ РС‚РѕРіРё РјРµСЃСЏС†Р°")

    # РіРѕРґ
    if local_date.month == 12 and local_date.day == 31:
        await _send("yearly_stats", "year", "рџ“Љ РС‚РѕРіРё РіРѕРґР°")

async def _send_holiday_notice_if_needed(bot: Bot, db, chat_id: int, session_id: int, local_date: date) -> None:
    holiday_text = None
    if local_date.month == 2 and local_date.day == 9:
        holiday_text = "Сегодня Национальный день какашек (National Poop Day)."
    elif local_date.month == 11 and local_date.day == 19:
        holiday_text = "Сегодня Всемирный день туалета (World Toilet Day)."

    if holiday_text is None:
        return

    q1_id = get_session_message_id(db, session_id, "Q1")
    q2_id = get_session_message_id(db, session_id, "Q2")
    q3_id = get_session_message_id(db, session_id, "Q3")
    if not (q1_id and q2_id and q3_id):
        return

    if get_command_message_id(db, chat_id, 0, "holiday_notice", local_date) is not None:
        return

    sent = await _safe_send_message(bot, chat_id=chat_id, text=holiday_text)
    set_command_message_id(db, chat_id, 0, "holiday_notice", local_date, sent.message_id)

