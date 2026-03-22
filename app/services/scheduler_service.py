from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, date, time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
)

from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from app.db.models import Chat, ChatMember, Session as DaySession, SessionUserState, User, UserStreak, PoopEvent
from app.db.session import db_session
from app.services.repo_service import (
    get_or_create_session,
    get_session_message_id,
    migrate_chat_settings,
    set_session_message_id,
)
from app.services.time_service import get_session_window, now_in_tz
from app.services.q1_service import mention, render_q1, render_q1_private
from app.services.q2_q3_service import ensure_q2_q3_exist, should_show_q2_q3_button
from app.services.scheduler_reports import (
    build_periodic_report_text,
    send_holiday_notice_if_needed,
    send_periodic_stats,
)
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.services.reminder_service import (
    LATE_REMINDER_COMMAND,
    build_late_reminder_text,
)
from app.services.scheduler_telegram import safe_edit_message_text, safe_send_message
from app.bot.keyboards.q1 import q1_keyboard
from app.bot.keyboards.recap import recap_announce_kb

logger = logging.getLogger(__name__)
_streak_recalc_date: dict[int, date] = {}
_q1_catchup_skip_date: dict[int, date] = {}
_CHAT_PROCESS_TIMEOUT_SEC = 25.0
_Q1_CATCHUP_MAX_DELAY_MIN = 180

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


def start_scheduler(
    bot: Bot,
    session_factory: sessionmaker,
    chat_throttle_sec: float = 0.2,
    tick_interval_sec: int = 60,
    q1_catchup_max_delay_min: int = 180,
) -> AsyncIOScheduler:
    global _Q1_CATCHUP_MAX_DELAY_MIN
    _Q1_CATCHUP_MAX_DELAY_MIN = max(1, q1_catchup_max_delay_min)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        func=_tick,
        trigger=IntervalTrigger(seconds=max(30, tick_interval_sec)),
        args=[bot, session_factory, chat_throttle_sec],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(30, tick_interval_sec),
    )
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


async def recover_missing_q1_on_startup(
    bot: Bot,
    session_factory: sessionmaker,
    chat_throttle_sec: float = 0.2,
) -> None:
    with db_session(session_factory) as db:
        chat_ids = list(db.scalars(select(Chat.chat_id).where(Chat.is_enabled == True)).all())

    recovered = 0
    for chat_id in chat_ids:
        try:
            posted = await asyncio.wait_for(
                _recover_chat_q1_on_startup(bot, session_factory, int(chat_id)),
                timeout=_CHAT_PROCESS_TIMEOUT_SEC,
            )
            if posted:
                recovered += 1
        except TelegramForbiddenError:
            with db_session(session_factory) as db:
                stale_chat = db.get(Chat, int(chat_id))
                if stale_chat is not None:
                    stale_chat.is_enabled = False
            logger.warning("Disabled chat during startup Q1 recovery after TelegramForbiddenError chat_id=%s", chat_id)
        except TelegramBadRequest as e:
            if _is_unreachable_chat_error(e):
                with db_session(session_factory) as db:
                    stale_chat = db.get(Chat, int(chat_id))
                    if stale_chat is not None:
                        stale_chat.is_enabled = False
                logger.warning("Disabled chat during startup Q1 recovery chat_id=%s error=%s", chat_id, e)
            else:
                logger.exception("Startup Q1 recovery bad request chat_id=%s", chat_id)
        except Exception:
            logger.exception("Startup Q1 recovery failed chat_id=%s", chat_id)
        if chat_throttle_sec > 0:
            await asyncio.sleep(chat_throttle_sec)

    logger.info("Startup Q1 recovery finished recovered=%s scanned=%s", recovered, len(chat_ids))


async def _tick(bot: Bot, session_factory: sessionmaker, chat_throttle_sec: float = 0.2) -> None:
    with db_session(session_factory) as db:
        chat_ids = list(db.scalars(select(Chat.chat_id).where(Chat.is_enabled == True)).all())

    for chat_id in chat_ids:
        try:
            await asyncio.wait_for(
                _process_chat(bot, session_factory, int(chat_id)),
                timeout=_CHAT_PROCESS_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.error("Scheduler chat processing timeout chat_id=%s (>%ss)", chat_id, _CHAT_PROCESS_TIMEOUT_SEC)
        except TelegramForbiddenError:
            # Bot no longer has access to this chat (kicked/blocked): stop scheduling it.
            with db_session(session_factory) as db:
                stale_chat = db.get(Chat, int(chat_id))
                if stale_chat is not None:
                    stale_chat.is_enabled = False
            logger.warning("Disabled chat after TelegramForbiddenError chat_id=%s", chat_id)
        except TelegramBadRequest as e:
            if _is_unreachable_chat_error(e):
                with db_session(session_factory) as db:
                    stale_chat = db.get(Chat, int(chat_id))
                    if stale_chat is not None:
                        stale_chat.is_enabled = False
                logger.warning("Disabled chat after TelegramBadRequest chat_id=%s error=%s", chat_id, e)
            else:
                logger.exception("Scheduler chat bad request chat_id=%s", chat_id)
        except TelegramMigrateToChat as e:
            with db_session(session_factory) as db:
                migrated = migrate_chat_settings(db, int(chat_id), e.migrate_to_chat_id)
            logger.warning(
                "Chat migrated to supergroup: old_chat_id=%s new_chat_id=%s migrated=%s",
                chat_id,
                e.migrate_to_chat_id,
                migrated is not None,
            )
        except Exception:
            logger.exception("Scheduler chat processing failed chat_id=%s", chat_id)
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
        now_min = local_time.hour * 60 + local_time.minute
        post_min = chat.post_time.hour * 60 + chat.post_time.minute
        late_min = 23 * 60 + 30
        periodic_min = 9 * 60
        close_cutoff = time(23, 55)
        notifications_enabled = bool(chat.notifications_enabled)
        late_reminder_enabled = bool(chat.late_reminder_enabled)
        q2_q3_enabled = bool(chat.q2_q3_enabled)

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
        # Catch-up mode: if exact minute was missed (network outage), post later in same session
        # while Q1 is still absent.
        if notifications_enabled and now_min >= post_min:
            q1_id = get_session_message_id(db, sess.session_id, "Q1")
            if not q1_id:
                delay_min = now_min - post_min
                if delay_min > _Q1_CATCHUP_MAX_DELAY_MIN:
                    if _q1_catchup_skip_date.get(chat_id) != window.session_date:
                        logger.warning(
                            "Skipping stale Q1 catch-up chat_id=%s delayed_by_min=%s max_delay_min=%s",
                            chat_id,
                            delay_min,
                            _Q1_CATCHUP_MAX_DELAY_MIN,
                        )
                        _q1_catchup_skip_date[chat_id] = window.session_date
                    return
                if delay_min > 0:
                    logger.warning(
                        "Q1 catch-up send chat_id=%s delayed_by_min=%s",
                        chat_id,
                        delay_min,
                    )
                await _post_q1(
                    bot,
                    db,
                    chat_id,
                    sess.session_id,
                    window.session_date,
                    q2_q3_enabled=q2_q3_enabled,
                    show_remind=(local_time < time(22, 0)),
                )

        # Catch-up for late reminder in the same active session.
        if notifications_enabled and late_reminder_enabled and now_min >= late_min:
            await _send_late_reminder(bot, db, chat_id, sess.session_id)

        # Периодические отчеты отправляем утром следующего дня (после закрытия периода).
        # Catch-up: если 09:00 было пропущено, отправим позже в тот же день один раз.
        if notifications_enabled and now_min >= periodic_min:
            await send_periodic_stats(bot, db, chat_id, local_date)

        if notifications_enabled:
            await send_holiday_notice_if_needed(bot, db, chat_id, sess.session_id, local_date)


async def _recover_chat_q1_on_startup(bot: Bot, session_factory: sessionmaker, chat_id: int) -> bool:
    with db_session(session_factory) as db:
        chat = db.get(Chat, chat_id)
        if chat is None or not chat.is_enabled or not bool(chat.notifications_enabled):
            return False

        window = get_session_window(chat.timezone)
        now_local = now_in_tz(chat.timezone)
        local_time = now_local.time()
        now_min = local_time.hour * 60 + local_time.minute
        post_min = chat.post_time.hour * 60 + chat.post_time.minute
        close_cutoff = time(23, 55)

        active_sessions = db.scalars(
            select(DaySession)
            .where(DaySession.chat_id == chat_id, DaySession.status == "active")
            .order_by(DaySession.session_date.asc())
        ).all()
        for active_sess in active_sessions:
            is_past_day = active_sess.session_date < window.session_date
            is_today_after_cutoff = active_sess.session_date == window.session_date and local_time >= close_cutoff
            if is_past_day or is_today_after_cutoff:
                await _close_session(bot, db, chat_id, active_sess.session_id, chat.timezone)

        sess = get_or_create_session(db, chat_id=chat_id, session_date=window.session_date)

        if local_time >= close_cutoff or sess.status == "closed" or window.is_blocked_window:
            return False
        if now_min < post_min:
            return False
        if get_session_message_id(db, sess.session_id, "Q1"):
            return False

        await _post_q1(
            bot,
            db,
            chat_id,
            sess.session_id,
            window.session_date,
            q2_q3_enabled=bool(chat.q2_q3_enabled),
            show_remind=(local_time < time(22, 0)),
        )
        logger.warning(
            "Recovered missing Q1 on startup chat_id=%s session_id=%s delayed_by_min=%s",
            chat_id,
            sess.session_id,
            max(0, now_min - post_min),
        )
        return True


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
            recap_sent = await safe_send_message(
                bot,
                chat_id=chat_id,
                text=recap_text,
                reply_markup=recap_announce_kb(),
            )
            # System marker: sent once per chat/day
            set_command_message_id(db, chat_id, 0, "recap_announce", session_date, recap_sent.message_id)

    has_any_members = bool(
        db.scalar(
            select(ChatMember.user_id).where(ChatMember.chat_id == chat_id).limit(1)
        )
    )

    text = (
        render_q1_private(db, chat_id=chat_id, session_id=session_id, user_id=chat_id, session_date=session_date)
        if chat_id > 0
        else render_q1(db, chat_id=chat_id, session_id=session_id, session_date=session_date)
    )
    sent = await safe_send_message(
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
    if q2_q3_enabled and chat_id < 0:
        try:
            await asyncio.wait_for(
                ensure_q2_q3_exist(bot, db, chat_id, session_id),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Q2/Q3 publish timeout chat_id=%s session_id=%s", chat_id, session_id)
    logger.info("Auto-posted Q1 chat_id=%s session_id=%s message_id=%s", chat_id, session_id, sent.message_id)


def _is_unreachable_chat_error(exc: TelegramBadRequest) -> bool:
    msg = str(exc).lower()
    return (
        "chat not found" in msg
        or "group chat was deleted" in msg
        or "group is deactivated" in msg
        or "bot was kicked from the group chat" in msg
    )


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

    sent = await safe_send_message(
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
    await safe_edit_message_text(bot, chat_id=chat_id, message_id=mid, text=text, reply_markup=None)


async def _lock_simple(bot: Bot, db, chat_id: int, session_id: int, kind: str, body_text: str) -> None:
    mid = get_session_message_id(db, session_id, kind)
    if not mid:
        return
    text = f"{LOCK_LINE}\n\n{body_text}"
    await safe_edit_message_text(bot, chat_id=chat_id, message_id=mid, text=text, reply_markup=None)


async def _lock_late_reminder(bot: Bot, db, chat_id: int, session_id: int) -> None:
    sess = db.get(DaySession, session_id)
    if sess is None:
        return
    mid = get_command_message_id(db, chat_id, 0, LATE_REMINDER_COMMAND, sess.session_date)
    if not mid:
        return

    body = build_late_reminder_text(db, session_id) or "⏳ Финальная напоминалка неактуальна."
    text = f"{LOCK_LINE}\n\n{body}"
    await safe_edit_message_text(
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
    await safe_edit_message_text(
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
