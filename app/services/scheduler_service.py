from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, date, time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from app.db.models import Chat, Session as DaySession, SessionUserState, ChatMember, User, UserStreak, UserGlobalStreak, PoopEvent
from app.db.session import db_session
from app.services.repo_service import (
    get_or_create_session,
    get_session_message_id,
    set_session_message_id,
)
from app.services.time_service import get_session_window, now_in_tz
from app.services.q1_service import mention, render_q1, render_q1_private
from app.services.q2_q3_service import ensure_q2_q3_exist, should_show_q2_q3_button
from app.services.stats_service import (
    build_stats_text_chat,
    compute_chat_period_metrics,
    period_to_range,
    previous_period_range,
    rank_chat_among_groups_by_total,
)
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.services.reminder_service import (
    LATE_REMINDER_COMMAND,
    build_late_reminder_text,
)
from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.recap import recap_announce_kb

logger = logging.getLogger(__name__)
_streak_recalc_date: dict[int, date] = {}
_global_streak_recalc_date: date | None = None
_CHAT_PROCESS_TIMEOUT_SEC = 25.0
_TELEGRAM_CALL_TIMEOUT_SEC = 15.0

LOCK_LINE = "🔒 Сессия закрыта."

Q2_TEXT = (
    "🧻 Бристоль (тип стула)\n"
    'Узнать о <a href="https://ru.wikipedia.org/wiki/Бристольская_шкала_формы_кала">шкале Бристоля</a>\n\n'
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


def start_scheduler(bot: Bot, session_factory: sessionmaker, chat_throttle_sec: float = 0.2) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        func=_tick,
        trigger=IntervalTrigger(seconds=30),
        args=[bot, session_factory, chat_throttle_sec],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


async def _safe_sleep_on_retry(exc: Exception) -> bool:
    if not isinstance(exc, TelegramRetryAfter):
        return False
    retry_after = exc.retry_after
    try:
        delay = float(retry_after) + 0.5
    except Exception:
        return False
    delay = max(0.5, min(delay, 60.0))
    logger.warning("Telegram rate limit hit. Sleeping %.1fs", delay)
    await asyncio.sleep(delay)
    return True


async def _safe_send_message(bot: Bot, **kwargs):
    for _ in range(3):
        try:
            return await asyncio.wait_for(
                bot.send_message(**kwargs),
                timeout=_TELEGRAM_CALL_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("send_message timeout after %.1fs", _TELEGRAM_CALL_TIMEOUT_SEC)
            continue
        except Exception as e:
            if await _safe_sleep_on_retry(e):
                continue
            raise
    raise TimeoutError("send_message failed after retries")


async def _safe_edit_message_text(bot: Bot, **kwargs):
    """
    Retry edit up to 3 times.
    Do not fail hard on:
    - message is not modified
    - message not found / to edit not found (message removed manually)
    """
    for _ in range(3):
        try:
            return await asyncio.wait_for(
                bot.edit_message_text(**kwargs),
                timeout=_TELEGRAM_CALL_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("edit_message_text timeout after %.1fs", _TELEGRAM_CALL_TIMEOUT_SEC)
            continue
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
    raise TimeoutError("edit_message_text failed after retries")


async def _tick(bot: Bot, session_factory: sessionmaker, chat_throttle_sec: float = 0.2) -> None:
    with db_session(session_factory) as db:
        chats = db.scalars(select(Chat).where(Chat.is_enabled == True)).all()

    for chat in chats:
        try:
            await asyncio.wait_for(
                _process_chat(bot, session_factory, chat.chat_id),
                timeout=_CHAT_PROCESS_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.error("Scheduler chat processing timeout chat_id=%s (>%ss)", chat.chat_id, _CHAT_PROCESS_TIMEOUT_SEC)
        except TelegramForbiddenError:
            # Bot no longer has access to this chat (kicked/blocked): stop scheduling it.
            with db_session(session_factory) as db:
                stale_chat = db.get(Chat, chat.chat_id)
                if stale_chat is not None:
                    stale_chat.is_enabled = False
            logger.warning("Disabled chat after TelegramForbiddenError chat_id=%s", chat.chat_id)
        except Exception:
            logger.exception("Scheduler chat processing failed chat_id=%s", chat.chat_id)
        if chat_throttle_sec > 0:
            await asyncio.sleep(chat_throttle_sec)


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
        notifications_enabled = bool(chat.notifications_enabled)
        late_reminder_enabled = bool(chat.late_reminder_enabled)
        q2_q3_enabled = bool(chat.q2_q3_enabled)

        # Recalculate once per day in a narrow low-traffic window,
        # so daytime polling is not impacted by heavy DB work.
        global _global_streak_recalc_date

        should_recalc_now = (
            local_time.hour == 0
            and 6 <= local_time.minute <= 10
            and _streak_recalc_date.get(chat_id) != local_date
        )
        should_recalc_global_now = (
            _global_streak_recalc_date is None
            or (
                local_time.hour == 0
                and 6 <= local_time.minute <= 10
                and _global_streak_recalc_date != local_date
            )
        )
        if should_recalc_now:
            _recalculate_streaks_from_history(db, chat_id, local_date)
            _streak_recalc_date[chat_id] = local_date
            await _refresh_current_q1_view(bot, db, chat_id, window.session_date)
        if should_recalc_global_now:
            _recalculate_global_streaks_from_history(db, local_date)
            _global_streak_recalc_date = local_date

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

        # 23:55 - close session
        if local_time >= close_cutoff:
            if sess.status != "closed":
                await _close_session(bot, db, chat_id, sess.session_id, chat.timezone)
            return

        if sess.status == "closed":
            return

        # 23:55-00:05 blocked window: do not post anything
        if window.is_blocked_window:
            return

        # РђРІС‚РѕРїРѕСЃС‚ Q1 РІ chat.post_time (СЂР°Р±РѕС‚Р°РµС‚ С‚РѕР»СЊРєРѕ РїРѕСЃР»Рµ РїРµСЂРІРѕРіРѕ /start,
        # РїРѕС‚РѕРјСѓ С‡С‚Рѕ Chat РїРѕСЏРІР»СЏРµС‚СЃСЏ РІ Р‘Р” С‚РѕР»СЊРєРѕ РєРѕРіРґР° РµРіРѕ СЃРѕР·РґР°Р»Рё РєРѕРјР°РЅРґРѕР№ /start РёР»Рё /help /stats)
        if notifications_enabled and local_time.hour == chat.post_time.hour and local_time.minute == chat.post_time.minute:
            q1_id = get_session_message_id(db, sess.session_id, "Q1")
            if not q1_id:
                await _post_q1(
                    bot,
                    db,
                    chat_id,
                    sess.session_id,
                    window.session_date,
                    q2_q3_enabled=q2_q3_enabled,
                    show_remind=(local_time < time(22, 0)),
                )

        if notifications_enabled and late_reminder_enabled and local_time.hour == 23 and local_time.minute == 30:
            await _send_late_reminder(bot, db, chat_id, sess.session_id)

        # 23:50 периодическая статистика (неделя/месяц/год), чтобы успеть учесть вечерние отметки.
        if notifications_enabled and local_time.hour == 23 and local_time.minute == 50:
            await _send_periodic_stats(bot, db, chat_id, local_date)

        if notifications_enabled:
            await _send_holiday_notice_if_needed(bot, db, chat_id, sess.session_id, local_date)


async def _post_q1(
    bot: Bot,
    db,
    chat_id: int,
    session_id: int,
    session_date,
    q2_q3_enabled: bool,
    show_remind: bool = True,
) -> None:
    if session_date.month == 12 and session_date.day == 30:
        sent_recap_mid = get_command_message_id(db, chat_id, 0, "recap_announce", session_date)
        if sent_recap_mid is None:
            recap_text = (
                "🎉 Доступен рекап года.\nЗапустить можно этой кнопкой или через `/stats`."
                if chat_id > 0
                else "🎉 Доступен рекап года. Забирай итоги!"
            )
            recap_sent = await _safe_send_message(
                bot,
                chat_id=chat_id,
                text=recap_text,
                reply_markup=recap_announce_kb(),
            )
            # System marker: sent once per chat/day
            set_command_message_id(db, chat_id, 0, "recap_announce", session_date, recap_sent.message_id)

    member_count = db.scalar(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == chat_id)) or 0
    has_any_members = member_count > 0

    text = (
        render_q1_private(db, chat_id=chat_id, session_id=session_id, user_id=chat_id, session_date=session_date)
        if chat_id > 0
        else render_q1(db, chat_id=chat_id, session_id=session_id, session_date=session_date)
    )
    sent = await _safe_send_message(
        bot,
        chat_id=chat_id,
        text=text,
        reply_markup=q1_keyboard(
            has_any_members,
            show_remind=show_remind,
            show_q2_q3_button=should_show_q2_q3_button(
                db,
                chat_q2_q3_enabled=bool(q2_q3_enabled),
                session_id=session_id,
                is_private_chat=chat_id > 0,
            ),
        ),
    )
    set_session_message_id(db, session_id, "Q1", sent.message_id)
    if q2_q3_enabled:
        try:
            await asyncio.wait_for(
                ensure_q2_q3_exist(bot, db, chat_id, session_id),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Q2/Q3 publish timeout chat_id=%s session_id=%s", chat_id, session_id)
    logger.info("Auto-posted Q1 chat_id=%s session_id=%s message_id=%s", chat_id, session_id, sent.message_id)


async def _send_late_reminder(bot: Bot, db, chat_id: int, session_id: int) -> None:
    q1_id = get_session_message_id(db, session_id, "Q1")
    if not q1_id:
        return
    sess = db.get(DaySession, session_id)
    if sess is None:
        return
    if get_command_message_id(db, chat_id, 0, LATE_REMINDER_COMMAND, sess.session_date) is not None:
        return

    text = build_late_reminder_text(db, session_id)
    if not text:
        return

    sent = await _safe_send_message(
        bot,
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_to_message_id=q1_id,
    )
    set_command_message_id(db, chat_id, 0, LATE_REMINDER_COMMAND, sess.session_date, sent.message_id)
    logger.info("Sent late reminder chat_id=%s session_id=%s", chat_id, session_id)


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

    positive_user_ids = {
        int(uid)
        for uid in db.scalars(
            select(PoopEvent.user_id)
            .where(PoopEvent.session_id == session_id, PoopEvent.origin_chat_id == chat_id)
            .group_by(PoopEvent.user_id)
        ).all()
    }

    local_date = sess.session_date

    for user_id, streak in member_rows:
        if int(user_id) in positive_user_ids:
            if streak.last_poop_date == (local_date - timedelta(days=1)):
                streak.current_streak += 1
            else:
                streak.current_streak = 1
            streak.last_poop_date = local_date
        else:
            streak.current_streak = 0

    # global streak: reset only if no marks in any chat for this session_date.
    chat_member_user_ids = [int(uid) for uid, _st in member_rows]
    if chat_member_user_ids:
        positive_global_user_ids = {
            int(uid)
            for uid in db.scalars(
                select(PoopEvent.user_id)
                .join(DaySession, DaySession.session_id == PoopEvent.session_id)
                .where(
                    DaySession.session_date == local_date,
                    PoopEvent.origin_chat_id == DaySession.chat_id,
                )
                .group_by(PoopEvent.user_id)
            ).all()
        }
        for uid in chat_member_user_ids:
            g = db.get(UserGlobalStreak, {"user_id": uid})
            if g is None:
                seed_streak, seed_last_date = _seed_global_streak_from_history(db, uid, local_date)
                g = UserGlobalStreak(
                    user_id=uid,
                    current_streak=seed_streak,
                    last_poop_date=seed_last_date,
                )
                db.add(g)

            if uid in positive_global_user_ids:
                if g.last_poop_date == local_date:
                    continue
                if g.last_poop_date == (local_date - timedelta(days=1)):
                    g.current_streak += 1
                else:
                    g.current_streak = 1
                g.last_poop_date = local_date
            else:
                g.current_streak = 0

    # Р»РѕС‡РёРј Q1/Q2/Q3 (РµСЃР»Рё СЃРѕРѕР±С‰РµРЅРёР№ РЅРµС‚ вЂ” СЃРїРѕРєРѕР№РЅРѕ РїСЂРѕРїСѓСЃРєР°РµРј)
    await _lock_q1(bot, db, chat_id, session_id)
    await _lock_simple(bot, db, chat_id, session_id, "Q2", Q2_TEXT)
    await _lock_simple(bot, db, chat_id, session_id, "Q3", Q3_TEXT)
    await _lock_late_reminder(bot, db, chat_id, session_id)

    logger.info("Closed session chat_id=%s session_id=%s", chat_id, session_id)


async def _lock_q1(bot: Bot, db, chat_id: int, session_id: int) -> None:
    mid = get_session_message_id(db, session_id, "Q1")
    if not mid:
        return

    sess = db.get(DaySession, session_id)
    text = (
        render_q1_private(db, chat_id=chat_id, session_id=session_id, user_id=chat_id, session_date=sess.session_date)
        if chat_id > 0
        else render_q1(db, chat_id=chat_id, session_id=session_id, session_date=sess.session_date)
    )
    text = f"{LOCK_LINE}\n\n{text}"
    await _safe_edit_message_text(bot, chat_id=chat_id, message_id=mid, text=text, reply_markup=None)


async def _lock_simple(bot: Bot, db, chat_id: int, session_id: int, kind: str, body_text: str) -> None:
    mid = get_session_message_id(db, session_id, kind)
    if not mid:
        return
    text = f"{LOCK_LINE}\n\n{body_text}"
    await _safe_edit_message_text(bot, chat_id=chat_id, message_id=mid, text=text, reply_markup=None)


async def _lock_late_reminder(bot: Bot, db, chat_id: int, session_id: int) -> None:
    sess = db.get(DaySession, session_id)
    if sess is None:
        return
    mid = get_command_message_id(db, chat_id, 0, LATE_REMINDER_COMMAND, sess.session_date)
    if not mid:
        return

    body = build_late_reminder_text(db, session_id) or "⏳ Финальная напоминалка неактуальна."
    text = f"{LOCK_LINE}\n\n{body}"
    await _safe_edit_message_text(
        bot,
        chat_id=chat_id,
        message_id=mid,
        text=text,
        parse_mode="HTML",
        reply_markup=None,
    )


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

    text = (
        render_q1_private(db, chat_id=chat_id, session_id=sess.session_id, user_id=chat_id, session_date=session_date)
        if chat_id > 0
        else render_q1(db, chat_id=chat_id, session_id=sess.session_id, session_date=session_date)
    )
    has_any_members = True if chat_id > 0 else ("Участники:" in text)
    chat = db.get(Chat, chat_id)
    show_remind = True
    show_q2_q3_button = False
    if chat is not None:
        show_remind = now_in_tz(chat.timezone).time() < time(22, 0)
        show_q2_q3_button = not bool(chat.q2_q3_enabled)
    await _safe_edit_message_text(
        bot,
        chat_id=chat_id,
        message_id=q1_id,
        text=text,
        reply_markup=q1_keyboard(
            has_any_members,
            show_remind=show_remind,
            show_q2_q3_button=should_show_q2_q3_button(
                db,
                chat_q2_q3_enabled=bool(chat.q2_q3_enabled) if chat is not None else False,
                session_id=sess.session_id,
                is_private_chat=chat_id > 0,
            ) if chat is not None else show_q2_q3_button,
        ),
    )


def _recalculate_streaks_from_history(db, chat_id: int, today: date) -> None:
    member_user_ids = db.scalars(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
    ).all()
    if not member_user_ids:
        return

    rows = db.execute(
        select(DaySession.session_date, PoopEvent.user_id)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.chat_id == chat_id,
            DaySession.session_date < today,
            PoopEvent.origin_chat_id == chat_id,
            PoopEvent.user_id.in_(member_user_ids),
        )
        .group_by(DaySession.session_date, PoopEvent.user_id)
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


def _recalculate_global_streaks_from_history(db, today: date) -> None:
    user_ids = [
        int(uid)
        for uid in db.scalars(select(ChatMember.user_id).group_by(ChatMember.user_id)).all()
    ]
    if not user_ids:
        return

    rows = db.execute(
        select(DaySession.session_date, PoopEvent.user_id)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.session_date < today,
            PoopEvent.origin_chat_id == DaySession.chat_id,
            PoopEvent.user_id.in_(user_ids),
        )
        .group_by(DaySession.session_date, PoopEvent.user_id)
        .order_by(DaySession.session_date.asc())
    ).all()

    days_by_user: dict[int, list[date]] = {uid: [] for uid in user_ids}
    for session_date, user_id in rows:
        uid = int(user_id)
        if not days_by_user[uid] or days_by_user[uid][-1] != session_date:
            days_by_user[uid].append(session_date)

    yesterday = today - timedelta(days=1)
    for uid in user_ids:
        g = db.get(UserGlobalStreak, {"user_id": uid})
        if g is None:
            g = UserGlobalStreak(user_id=uid, current_streak=0, last_poop_date=None)
            db.add(g)

        days = days_by_user.get(uid, [])
        if not days:
            g.current_streak = 0
            g.last_poop_date = None
            continue

        last_day = days[-1]
        trailing = 1
        idx = len(days) - 2
        while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
            trailing += 1
            idx -= 1

        g.last_poop_date = last_day
        g.current_streak = trailing if last_day == yesterday else 0


def _seed_global_streak_from_history(db, user_id: int, today: date) -> tuple[int, date | None]:
    days = [
        d
        for d in db.scalars(
            select(DaySession.session_date)
            .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
            .where(
                DaySession.session_date < today,
                PoopEvent.user_id == user_id,
                PoopEvent.origin_chat_id == DaySession.chat_id,
            )
            .group_by(DaySession.session_date)
            .order_by(DaySession.session_date.asc())
        ).all()
    ]
    if not days:
        return 0, None

    last_day = days[-1]
    if last_day != (today - timedelta(days=1)):
        return 0, last_day

    trailing = 1
    idx = len(days) - 2
    while idx >= 0 and days[idx] == (days[idx + 1] - timedelta(days=1)):
        trailing += 1
        idx -= 1
    return trailing, last_day


def _is_last_day_of_month(d: date) -> bool:
    return (d + timedelta(days=1)).month != d.month


def build_periodic_report_text(db, chat_id: int, local_date: date, period: str, title: str) -> str:
    def _trend_line(label: str, curr: int, prev: int) -> str:
        delta = curr - prev
        if delta > 0:
            sign = "📈"
        elif delta < 0:
            sign = "📉"
        else:
            sign = "➖"
        if prev > 0:
            pct = (float(delta) / float(prev)) * 100.0
            return f"- {label}: {curr} ({sign} {delta:+d}, {pct:+.1f}% к прошлому периоду)"
        if curr > 0:
            return f"- {label}: {curr} (🆕 в прошлом периоде было 0)"
        return f"- {label}: {curr} (➖ без изменений)"

    def _rank_line(curr_rank: int | None, prev_rank: int | None, total_chats: int) -> str:
        if curr_rank is None or total_chats <= 0:
            return "- Место чата среди чатов: нет данных за период"
        if prev_rank is None:
            return f"- Место чата среди чатов: #{curr_rank} из {total_chats}"
        delta = prev_rank - curr_rank
        if delta > 0:
            trend = f"📈 +{delta}"
        elif delta < 0:
            trend = f"📉 {delta}"
        else:
            trend = "➖ 0"
        return f"- Место чата среди чатов: #{curr_rank} из {total_chats} ({trend} к прошлому периоду)"

    is_private = chat_id > 0
    report_user_id = None
    if is_private:
        report_user_id = db.scalar(
            select(ChatMember.user_id)
            .where(ChatMember.chat_id == chat_id)
            .order_by(ChatMember.joined_at.asc())
            .limit(1)
        )
        if report_user_id is None:
            report_user_id = chat_id
    curr_r = period_to_range(local_date, period)
    prev_r = previous_period_range(local_date, period)

    text = title + "\n\n" + build_stats_text_chat(db, chat_id, local_date, period, user_id=report_user_id)

    curr_metrics = compute_chat_period_metrics(db, chat_id, curr_r, user_id=report_user_id)
    prev_metrics = compute_chat_period_metrics(db, chat_id, prev_r, user_id=report_user_id)

    trend_lines = ["Тенденция к прошлому периоду:"]
    trend_lines.append(_trend_line("Всего 💩", curr_metrics.total_poops, prev_metrics.total_poops))
    trend_lines.append(
        _trend_line(
            "Активных дней",
            curr_metrics.active_days_count,
            prev_metrics.active_days_count,
        )
    )
    if not is_private:
        trend_lines.append(
            _trend_line(
                "Активных участников",
                curr_metrics.active_participants,
                prev_metrics.active_participants,
            )
        )
        curr_rank, total_chats = rank_chat_among_groups_by_total(db, chat_id, curr_r)
        prev_rank, _ = rank_chat_among_groups_by_total(db, chat_id, prev_r)
        trend_lines.append(_rank_line(curr_rank, prev_rank, total_chats))

    text = text + "\n\n" + "\n".join(trend_lines)

    if not is_private:
        praise_block = _build_streak_praise_block(db, chat_id)
        if praise_block:
            text = text + "\n\n" + praise_block
    return text


async def _send_periodic_stats(bot: Bot, db, chat_id: int, local_date: date) -> None:
    # используем user_id=0 как системную метку, чтобы не дублировать отправки
    def _already_sent(kind: str) -> bool:
        return get_command_message_id(db, chat_id, 0, kind, local_date) is not None

    async def _send(kind: str, period: str, title: str) -> None:
        if _already_sent(kind):
            return
        text = build_periodic_report_text(db, chat_id=chat_id, local_date=local_date, period=period, title=title)
        sent = await _safe_send_message(bot, chat_id=chat_id, text=text)
        set_command_message_id(db, chat_id, 0, kind, local_date, sent.message_id)

    # РЅРµРґРµР»СЏ: СЃС‡РёС‚Р°РµРј РєРѕРЅС†РѕРј РЅРµРґРµР»Рё РІРѕСЃРєСЂРµСЃРµРЅСЊРµ (weekday=6)
    if local_date.weekday() == 6:
        await _send("weekly_stats", "week", "📉 Итоги недели")

    # РјРµСЃСЏС†
    if _is_last_day_of_month(local_date):
        await _send("monthly_stats", "month", "📉 Итоги месяца")

    # РіРѕРґ
    if local_date.month == 12 and local_date.day == 31:
        await _send("yearly_stats", "year", "📉 Итоги года")


def _streak_rank_label(days: int) -> str:
    if days >= 365:
        return "🌟 Легенда стрика"
    if days >= 180:
        return "👑 Полугодовой чемпион"
    if days >= 90:
        return "💪 Квартальный титан"
    if days >= 30:
        return "🏅 Месячный монолит"
    if days >= 7:
        return "🔥 Железная неделя"
    return "👏 Держит ритм"


def _build_streak_praise_block(db, chat_id: int) -> str | None:
    rows = db.execute(
        select(UserStreak.user_id, UserStreak.current_streak, User)
        .join(
            ChatMember,
            (ChatMember.chat_id == UserStreak.chat_id) & (ChatMember.user_id == UserStreak.user_id),
        )
        .join(User, User.user_id == UserStreak.user_id)
        .where(UserStreak.chat_id == chat_id, UserStreak.current_streak > 0)
        .order_by(UserStreak.current_streak.desc(), UserStreak.user_id.asc())
        .limit(10)
    ).all()
    if not rows:
        return None

    lines = ["👏 Кто держит стрик:"]
    for user_id, streak_days, user in rows:
        days = int(streak_days or 0)
        if days <= 0:
            continue
        lines.append(f"- {_streak_rank_label(days)}: {mention(user)} — {days} дн.")

    return "\n".join(lines) if len(lines) > 1 else None

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
