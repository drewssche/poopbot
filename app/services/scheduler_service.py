from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

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
from app.services.q1_service import render_q1, mention
from app.services.q2_q3_service import ensure_q2_q3_exist
from app.services.stats_service import build_stats_text_chat
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.bot.keyboards.q1 import q1_keyboard

logger = logging.getLogger(__name__)

LOCK_LINE = "🔒 Сессия закрыта."

Q2_TEXT = (
    "🧻 Бристоль (тип стула)\n"
    "🧱 1–2 (жёстко / сухо)\n"
    "🍌 3–4 (норма)\n"
    "🍦 5–6 (мягко)\n"
    "💦 7 (водичка)"
)

Q3_TEXT = (
    "😮‍💨 Как прошёл процесс?\n"
    "😇 Прекрасно\n"
    "😐 Сойдёт\n"
    "😫 Ужасно"
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
    Пытаемся 3 раза. Не валимся на:
    - message is not modified
    - message not found / to edit not found (когда удалили руками)
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

        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        # 23:55 — закрыть
        if local_time.hour == 23 and local_time.minute == 55:
            if sess.status != "closed":
                await _close_session(bot, db, chat_id, sess.session_id, chat.timezone)
            return

        if sess.status == "closed":
            return

        # 23:55–00:05 (blocked window): ничего не постим
        if window.is_blocked_window:
            return

        # Автопост Q1 в chat.post_time (работает только после первого /start,
        # потому что Chat появляется в БД только когда его создали командой /start или /help /stats)
        if local_time.hour == chat.post_time.hour and local_time.minute == chat.post_time.minute:
            q1_id = get_session_message_id(db, sess.session_id, "Q1")
            if not q1_id:
                await _post_q1(bot, db, chat_id, sess.session_id, window.session_date)

        # 22:00 напоминалка (один раз)
        if local_time.hour == 22 and local_time.minute == 0 and not sess.reminded_22_sent:
            await _send_reminder_22(bot, db, chat_id, sess.session_id)
            sess.reminded_22_sent = True

        # 23:00 периодическая статистика (неделя/месяц/год)
        if local_time.hour == 23 and local_time.minute == 0:
            await _send_periodic_stats(bot, db, chat_id, local_date)


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
    mentions = [mention(u) for u in users]

    text = "⏰ Напоминалка на 22:00:\n" + " ".join(mentions)
    await _safe_send_message(bot, chat_id=chat_id, text=text, reply_to_message_id=q1_id)
    logger.info("Sent 22:00 reminder chat_id=%s session_id=%s", chat_id, session_id)


async def _close_session(bot: Bot, db, chat_id: int, session_id: int, tz_name: str) -> None:
    sess = db.get(DaySession, session_id)
    if sess is None or sess.status == "closed":
        return

    sess.status = "closed"
    sess.end_at = datetime.utcnow()

    # стрики: если сегодня poops_n > 0 → +1 день подряд, иначе сброс
    member_rows = db.execute(
        select(UserStreak.user_id, UserStreak).where(UserStreak.chat_id == chat_id)
    ).all()

    states = {
        s.user_id: s
        for s in db.scalars(select(SessionUserState).where(SessionUserState.session_id == session_id)).all()
    }

    local_date = now_in_tz(tz_name).date()

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

    # лочим Q1/Q2/Q3 (если сообщений нет — спокойно пропускаем)
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


def _is_last_day_of_month(d: date) -> bool:
    return (d + timedelta(days=1)).month != d.month


async def _send_periodic_stats(bot: Bot, db, chat_id: int, local_date: date) -> None:
    # используем user_id=0 как системную метку, чтобы не дублировать отправки
    def _already_sent(kind: str) -> bool:
        return get_command_message_id(db, chat_id, 0, kind, local_date) is not None

    async def _send(kind: str, period: str, title: str) -> None:
        if _already_sent(kind):
            return
        text = title + "\n\n" + build_stats_text_chat(db, chat_id, local_date, period)
        sent = await _safe_send_message(bot, chat_id=chat_id, text=text)
        set_command_message_id(db, chat_id, 0, kind, local_date, sent.message_id)

    # неделя: считаем концом недели воскресенье (weekday=6)
    if local_date.weekday() == 6:
        await _send("weekly_stats", "week", "📊 Итоги недели")

    # месяц
    if _is_last_day_of_month(local_date):
        await _send("monthly_stats", "month", "📊 Итоги месяца")

    # год
    if local_date.month == 12 and local_date.day == 31:
        await _send("yearly_stats", "year", "📊 Итоги года")
